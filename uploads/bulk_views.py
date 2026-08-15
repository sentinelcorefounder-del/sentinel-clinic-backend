import uuid

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from encounters.models import AssessmentServiceSession, ScreeningEncounter
from organizations.models import OrganizationBranch
from uploads.bulk_import import (
    _safe_audit,
    assert_import_scope,
    cleanup_import,
    confirm_bulk_import,
    create_bulk_import,
)
from uploads.bulk_serializers import BulkImageImportSerializer
from uploads.models import BulkImageImport, BulkImageImportGroup, BulkImageImportItem
from uploads.storage import get_bulk_staging_storage
from organizations.services.branches import accessible_branch_ids
from uploads.bulk_import import _organization_for


def _import_for(user, import_id, write=False):
    bulk_import = get_object_or_404(
        BulkImageImport.objects.select_related("organization", "branch", "service_session"),
        import_id=import_id,
    )
    assert_import_scope(user, bulk_import, write=write)
    return bulk_import


class BulkImageImportCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        get_bulk_staging_storage()  # fail closed before accepting content
        archive = request.FILES.get("archive")
        if not archive or not archive.name.lower().endswith(".zip"):
            raise ValidationError("Select one ZIP archive.")
        session = get_object_or_404(AssessmentServiceSession, pk=request.data.get("service_session"))
        branch = get_object_or_404(OrganizationBranch, pk=request.data.get("branch"))
        if session.participating_organization_id != branch.organization_id or session.service_branch_id != branch.id:
            raise ValidationError("Session and branch do not match.")
        key = request.headers.get("Idempotency-Key") or request.data.get("idempotency_key") or uuid.uuid4().hex
        bulk_import = create_bulk_import(user=request.user, service_session=session, branch=branch, uploaded_file=archive, idempotency_key=key)
        return Response(BulkImageImportSerializer(bulk_import).data, status=status.HTTP_201_CREATED)


class BulkImageImportSessionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        roles = set(request.user.groups.values_list("name", flat=True))
        if not (request.user.is_superuser or roles & {"clinic_screener", "clinic_admin", "super_admin", "ops_admin", "sentinel_ops"}):
            raise ValidationError("You do not have permission to manage image imports.")
        queryset = AssessmentServiceSession.objects.select_related("participating_organization", "service_branch").filter(status__in=["active", "completed"], service_branch__isnull=False)
        organization = _organization_for(request.user)
        if not (request.user.is_superuser or roles & {"ops_admin", "sentinel_ops", "super_admin"}):
            queryset = queryset.filter(participating_organization=organization)
            branch_ids = accessible_branch_ids(request.user, organization)
            if branch_ids is not None:
                queryset = queryset.filter(service_branch_id__in=branch_ids)
        return Response([
            {
                "id": row.id,
                "session_reference": row.session_reference,
                "service_date": row.service_date,
                "organization": row.participating_organization.name,
                "branch": row.service_branch.name,
                "branch_id": row.service_branch_id,
                "status": row.status,
            }
            for row in queryset.order_by("-service_date", "-created_at")
        ])


class BulkImageImportDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, import_id):
        return Response(BulkImageImportSerializer(_import_for(request.user, import_id)).data)

    def delete(self, request, import_id):
        bulk_import = _import_for(request.user, import_id, write=True)
        if bulk_import.status == "confirmed":
            raise ValidationError("A confirmed import cannot be cancelled.")
        with transaction.atomic():
            bulk_import = BulkImageImport.objects.select_for_update().get(pk=bulk_import.pk)
            bulk_import.status = "cancelled"
            bulk_import.cleanup_pending = True
            bulk_import.save(update_fields=["status", "cleanup_pending", "updated_at"])
            _safe_audit(request.user, "bulk_import_cancelled", bulk_import, cleanup_pending=True)
        cleanup_import(bulk_import)
        return Response(BulkImageImportSerializer(bulk_import).data)


class BulkImageImportGroupResolveView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, import_id, group_id):
        bulk_import = _import_for(request.user, import_id, write=True)
        if bulk_import.status != "preview" or bulk_import.expires_at <= timezone.now():
            raise ValidationError("This import is not editable.")
        group = get_object_or_404(BulkImageImportGroup, bulk_import=bulk_import, group_id=group_id)
        encounter_id = request.data.get("encounter")
        if encounter_id:
            encounter = get_object_or_404(
                ScreeningEncounter.objects.select_related("patient"),
                pk=encounter_id, service_session=bulk_import.service_session,
                service_branch=bulk_import.branch,
            )
            group.resolved_encounter = encounter
            group.resolved_by = request.user
            group.resolved_at = timezone.now()
            group.status = "resolved"
            group.safe_issue_code = ""
            group.save(update_fields=["resolved_encounter", "resolved_by", "resolved_at", "status", "safe_issue_code"])
            _safe_audit(request.user, "bulk_import_group_resolved", bulk_import, group_id=str(group.group_id), encounter_id=encounter.encounter_id)
        decisions = request.data.get("decisions", {})
        if not isinstance(decisions, dict):
            raise ValidationError("Decisions must be an object.")
        for item_id, decision in decisions.items():
            if decision not in {"left", "right", "rejected"}:
                raise ValidationError("Use left, right, or rejected.")
            item = get_object_or_404(BulkImageImportItem, group=group, item_id=item_id)
            if item.decision in {"skipped", "invalid"}:
                raise ValidationError("This item cannot be selected.")
            item.decision = decision
            item.save(update_fields=["decision"])
        if decisions:
            _safe_audit(request.user, "bulk_import_laterality_resolved", bulk_import, group_id=str(group.group_id), decisions=len(decisions))
        return Response(BulkImageImportSerializer(bulk_import).data)


class BulkImageImportEncounterSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, import_id):
        bulk_import = _import_for(request.user, import_id, write=True)
        search = (request.query_params.get("search") or "").strip()
        queryset = ScreeningEncounter.objects.select_related("patient", "patient__master_patient").filter(
            service_session=bulk_import.service_session,
            service_branch=bulk_import.branch,
        )
        if search:
            queryset = queryset.filter(
                Q(encounter_id__icontains=search)
                | Q(patient__patient_id__icontains=search)
                | Q(patient__master_patient__sentinel_patient_id__icontains=search)
                | Q(patient__hospital_referrals__referral_id__icontains=search)
                | Q(patient__hospital_referrals__hospital_mrn__icontains=search)
            )
        return Response([
            {
                "id": row.id,
                "encounter_id": row.encounter_id,
                "patient_name": f"{row.patient.first_name} {row.patient.last_name}".strip(),
                "sentinel_patient_id": row.patient.master_patient.sentinel_patient_id if row.patient.master_patient_id else "",
            }
            for row in queryset.distinct().order_by("encounter_id")[:50]
        ])


class BulkImageImportPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, import_id, item_id):
        bulk_import = _import_for(request.user, import_id)
        if bulk_import.status not in {"preview", "confirmed"} or bulk_import.expires_at <= timezone.now():
            raise ValidationError("This preview is unavailable.")
        item = get_object_or_404(BulkImageImportItem, group__bulk_import=bulk_import, item_id=item_id)
        if not item.staged_object_key:
            raise ValidationError("This item has no preview.")
        response = FileResponse(get_bulk_staging_storage().open(item.staged_object_key, "rb"), content_type="image/jpeg" if item.detected_format == "JPEG" else "image/png")
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = "inline"
        return response


class BulkImageImportConfirmView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, import_id):
        bulk_import = confirm_bulk_import(user=request.user, import_id=import_id)
        return Response(BulkImageImportSerializer(bulk_import).data)
