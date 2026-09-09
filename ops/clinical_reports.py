from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from encounters.models import OcularDiagnosticAssessment
from encounters.serializers import OcularDiagnosticAssessmentSerializer
from reports.models import EyeHealthScreeningReport, StructuredReport
from reports.permissions import has_internal_ops_authority
from reports.serializers import EyeHealthScreeningReportSerializer


def _require_ops(request):
    if not has_internal_ops_authority(request.user):
        return Response({"detail": "Exact Sentinel Ops authority is required."}, status=status.HTTP_403_FORBIDDEN)
    return None


def _patient_name(patient):
    return " ".join(part for part in [patient.first_name, patient.last_name] if part).strip() or patient.patient_id


def _referral(encounter):
    return getattr(encounter, "hospital_referral", None)


def _row(kind, object_id, encounter, status_value, submitted_at, signer_snapshot=None):
    patient = encounter.patient
    referral = _referral(encounter)
    clinic = patient.assigned_clinic
    hospital = referral.source_hospital if referral else None
    return {
        "kind": kind,
        "id": object_id,
        "encounter_id": encounter.id,
        "encounter_reference": encounter.encounter_id,
        "patient_id": patient.id,
        "patient_reference": patient.patient_id,
        "patient_name": _patient_name(patient),
        "clinic_id": clinic.id if clinic else None,
        "clinic_name": clinic.name if clinic else "",
        "hospital_id": hospital.id if hospital else None,
        "hospital_name": hospital.name if hospital else "",
        "status": status_value,
        "submitted_at": submitted_at,
        "signer_snapshot": signer_snapshot or {},
    }


class OpsClinicalReportQueueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_ops(request)
        if denied:
            return denied
        requested_status = (request.query_params.get("status") or "awaiting_ops").strip()
        search = (request.query_params.get("search") or "").strip()
        rows = []

        diabetic = StructuredReport.objects.select_related(
            "encounter__patient__assigned_clinic",
            "encounter__hospital_referral__source_hospital",
            "clinical_responsibility",
        )
        if requested_status != "all":
            diabetic_status = "submitted_to_ops" if requested_status == "awaiting_ops" else requested_status
            diabetic = diabetic.filter(report_status=diabetic_status)
        if search:
            diabetic = diabetic.filter(
                Q(report_id__icontains=search)
                | Q(patient__patient_id__icontains=search)
                | Q(patient__first_name__icontains=search)
                | Q(patient__last_name__icontains=search)
                | Q(encounter__encounter_id__icontains=search)
            )
        for report in diabetic.order_by("-submitted_to_ops_at", "-created_at")[:500]:
            responsibility = getattr(report, "clinical_responsibility", None)
            signer = {
                "display_name": getattr(responsibility, "clinician_name", "") if responsibility else "",
                "professional_role": getattr(responsibility, "professional_role", "") if responsibility else "",
                "registration_number": getattr(responsibility, "registration_number", "") if responsibility else "",
            }
            rows.append(_row("diabetic", report.id, report.encounter, report.report_status, report.submitted_to_ops_at, signer))

        eye_health = EyeHealthScreeningReport.objects.select_related(
            "encounter__patient__assigned_clinic",
            "encounter__hospital_referral__source_hospital",
            "finalized_version",
        )
        if requested_status != "all":
            eye_status = (
                EyeHealthScreeningReport.ReviewStatus.AWAITING_OPS if requested_status == "awaiting_ops"
                else EyeHealthScreeningReport.ReviewStatus.APPROVED if requested_status == "issued"
                else requested_status
            )
            eye_health = eye_health.filter(review_status=eye_status)
        if search:
            eye_health = eye_health.filter(
                Q(encounter__encounter_id__icontains=search)
                | Q(encounter__patient__patient_id__icontains=search)
                | Q(encounter__patient__first_name__icontains=search)
                | Q(encounter__patient__last_name__icontains=search)
            )
        for report in eye_health.order_by("-submitted_to_ops_at", "-created_at")[:500]:
            signer = report.finalized_version.clinician_snapshot if report.finalized_version_id else {}
            rows.append(_row("eye_health", report.id, report.encounter, report.review_status, report.submitted_to_ops_at, signer))

        ocular = OcularDiagnosticAssessment.objects.select_related(
            "encounter__patient__assigned_clinic",
            "encounter__hospital_referral__source_hospital",
            "current_version",
        )
        if requested_status != "all":
            ocular_status = "awaiting_ops" if requested_status == "awaiting_ops" else requested_status
            ocular = ocular.filter(report_status=ocular_status)
        if search:
            ocular = ocular.filter(
                Q(encounter__encounter_id__icontains=search)
                | Q(encounter__patient__patient_id__icontains=search)
                | Q(encounter__patient__first_name__icontains=search)
                | Q(encounter__patient__last_name__icontains=search)
            )
        for assessment in ocular.order_by("-submitted_to_ops_at", "-created_at")[:500]:
            signer = assessment.current_version.clinician_snapshot if assessment.current_version_id else assessment.signer_snapshot
            rows.append(_row("ocular", assessment.id, assessment.encounter, assessment.report_status, assessment.submitted_to_ops_at, signer))

        rows.sort(key=lambda item: item["submitted_at"].timestamp() if item["submitted_at"] else 0, reverse=True)
        return Response(rows[:500])


class OpsEyeHealthReportDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        denied = _require_ops(request)
        if denied:
            return denied
        report = EyeHealthScreeningReport.objects.select_related(
            "encounter__patient__assigned_clinic",
            "encounter__hospital_referral__source_hospital",
            "finalized_version",
        ).filter(pk=pk).first()
        if not report:
            return Response({"detail": "Targeted screening report not found."}, status=404)
        encounter = report.encounter
        referral = _referral(encounter)
        return Response({
            "report": EyeHealthScreeningReportSerializer(report, context={"request": request}).data,
            "signed_snapshot": report.finalized_version.clinical_snapshot if report.finalized_version_id else {},
            "signed_clinician": report.finalized_version.clinician_snapshot if report.finalized_version_id else {},
            "context": {
                "encounter_id": encounter.id,
                "encounter_reference": encounter.encounter_id,
                "patient_id": encounter.patient_id,
                "patient_name": _patient_name(encounter.patient),
                "patient_reference": encounter.patient.patient_id,
                "clinic_name": encounter.patient.assigned_clinic.name if encounter.patient.assigned_clinic else "",
                "hospital_name": referral.source_hospital.name if referral else "",
            },
        })


class OpsOcularReportDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        denied = _require_ops(request)
        if denied:
            return denied
        assessment = OcularDiagnosticAssessment.objects.select_related(
            "encounter__patient__assigned_clinic",
            "encounter__hospital_referral__source_hospital",
            "current_version",
        ).filter(encounter_id=encounter_id).first()
        if not assessment:
            return Response({"detail": "Ocular report not found."}, status=404)
        encounter = assessment.encounter
        referral = _referral(encounter)
        signed_snapshot = assessment.current_version.clinical_snapshot if assessment.current_version_id else {}
        signed_clinician = assessment.current_version.clinician_snapshot if assessment.current_version_id else assessment.signer_snapshot
        return Response({
            "assessment": OcularDiagnosticAssessmentSerializer(assessment, context={"request": request}).data,
            "signed_snapshot": signed_snapshot,
            "signed_clinician": signed_clinician,
            "context": {
                "encounter_id": encounter.id,
                "encounter_reference": encounter.encounter_id,
                "patient_id": encounter.patient_id,
                "patient_name": _patient_name(encounter.patient),
                "patient_reference": encounter.patient.patient_id,
                "clinic_name": encounter.patient.assigned_clinic.name if encounter.patient.assigned_clinic else "",
                "hospital_name": referral.source_hospital.name if referral else "",
            },
        })
