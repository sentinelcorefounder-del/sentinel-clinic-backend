from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from organizations.services.branches import accessible_branch_ids
from reports.permissions import has_internal_ops_authority
from users.clinical_authority import exact_clinical_authority

from encounters.models import ScreeningEncounter
from .models import DiabeticRecall, HistoricalReportDocument
from .recall_services import calculate_diabetic_recall_live_status, set_diabetic_recall


def _scoped_recalls(request):
    qs = DiabeticRecall.objects.select_related(
        "patient", "organization", "encounter", "encounter__service_branch", "historical_report"
    )
    if has_internal_ops_authority(request.user):
        return qs
    org = get_user_organization(request.user)
    if not org or org.organization_type != "clinic":
        return qs.none()
    qs = qs.filter(organization=org)
    branch_ids = accessible_branch_ids(request.user, org)
    if branch_ids is not None:
        qs = qs.filter(Q(encounter__service_branch_id__in=branch_ids) | Q(encounter__service_branch__isnull=True, patient__assigned_branch_id__in=branch_ids))
    return qs


def _serialize(recall):
    return {
        "id": recall.id,
        "report_id": recall.historical_report.historical_report_id if recall.historical_report_id else recall.encounter.encounter_id,
        "source_type": recall.source_type,
        "encounter_id": recall.encounter_id,
        "patient_pk": recall.patient_id,
        "patient_id": recall.patient.patient_id,
        "sentinel_patient_id": getattr(getattr(recall.patient, "master_patient", None), "sentinel_patient_id", None),
        "patient_name": f"{recall.patient.first_name} {recall.patient.last_name}".strip(),
        "patient_email": recall.patient.email,
        "clinic_name": recall.organization.name,
        "recall_months": recall.recall_months,
        "recall_due_date": recall.due_date,
        "recall_status": calculate_diabetic_recall_live_status(recall),
        "recall_note": recall.note,
        "base_date": recall.base_date,
    }


class RecallQueueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        status_filter = (request.query_params.get("status") or "").strip()
        data = []
        for recall in _scoped_recalls(request).order_by("due_date", "id"):
            live_status = calculate_diabetic_recall_live_status(recall)
            if status_filter and status_filter != "all" and live_status != status_filter:
                continue
            data.append(_serialize(recall))
        return Response(data)


class EncounterRecallView(APIView):
    permission_classes = [IsAuthenticated]

    def _encounter(self, request, encounter_id):
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related("patient__assigned_clinic", "patient__assigned_branch", "service_branch"),
            pk=encounter_id,
        )
        org = get_user_organization(request.user)
        if not org or org.organization_type != "clinic" or encounter.patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You do not have access to this encounter.")
        branch_ids = accessible_branch_ids(request.user, org)
        branch_id = encounter.service_branch_id or encounter.patient.assigned_branch_id
        if branch_ids is not None and branch_id not in branch_ids:
            raise PermissionDenied("You do not have access to this encounter branch.")
        return encounter

    def get(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        recall = DiabeticRecall.objects.select_related("patient", "organization", "historical_report", "encounter").filter(encounter=encounter).first()
        return Response(_serialize(recall) if recall else None)

    def post(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        if not exact_clinical_authority(request.user):
            raise PermissionDenied("Exact optometrist or qualified reviewer authority is required to set diabetic recall.")
        months = request.data.get("recall_months")
        historical_report = None
        historical_id = request.data.get("historical_report_id")
        if historical_id:
            historical_report = get_object_or_404(HistoricalReportDocument, pk=historical_id, encounter=encounter, patient=encounter.patient)
        try:
            recall = set_diabetic_recall(
                encounter=encounter, months=months, actor=request.user,
                historical_report=historical_report, note=(request.data.get("note") or "").strip(),
            )
        except Exception as exc:
            detail = getattr(exc, "messages", None) or [str(exc)]
            return Response({"detail": detail}, status=400)
        recall = DiabeticRecall.objects.select_related("patient", "organization", "historical_report", "encounter").get(pk=recall.pk)
        return Response(_serialize(recall))


class RecallActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        recall = _scoped_recalls(request).filter(pk=pk).first()
        if not recall:
            return Response({"detail": "Recall not found."}, status=status.HTTP_404_NOT_FOUND)
        action = (request.data.get("action") or "").strip()
        note = (request.data.get("note") or "").strip()
        now = timezone.now()
        if action == "contacted":
            recall.status = "contacted"; recall.contacted_at = now
        elif action == "booked":
            recall.status = "booked"; recall.booked_at = now
        elif action == "completed":
            recall.status = "completed"; recall.completed_at = now
        elif action == "deferred":
            recall.status = "deferred"; recall.deferred_at = now
        elif action == "send_email":
            if not recall.patient.email:
                return Response({"detail": "Patient email is missing."}, status=400)
            send_mail(
                subject="Sentinel diabetic eye assessment recall reminder",
                message=(f"Hello {recall.patient.first_name},\n\nYour diabetic eye assessment recall is due on {recall.due_date}.\n\nPlease contact your clinic to arrange an appointment.\n\nSentinel Health"),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[recall.patient.email], fail_silently=False,
            )
            recall.status = "contacted"; recall.contacted_at = now
        else:
            return Response({"detail": "action must be contacted, booked, completed, deferred or send_email."}, status=400)
        recall.note = note
        recall.updated_by = request.user
        recall.save()
        return Response({"message": "Recall updated.", "recall_id": recall.id, "recall_status": recall.status})
