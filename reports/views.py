from io import BytesIO
import os
import hashlib

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from organizations.models import OrganizationProfile
from uploads.models import ImageUpload
from .models import (
    EyeHealthScreeningReport, ReportClinicalResponsibility,
    StructuredReport, ReportStatusEvent,
)
from .clinical_integrity import (
    CLINICAL_FIELDS,
    accept_responsibility,
    assert_expected,
    bind_issued_pdf,
    create_version_if_changed,
    delete_bound_pdf,
    event_once,
    expected_version,
    latest_version,
    require_responsible_clinician,
)
from .clinical_wording import apply_generated_wording
from .recall_services import apply_recall_schedule
from .permissions import (
    CanManageReports,
    CanReviewOpsReports,
    CanSubmitReportToOps,
)
from .serializers import EyeHealthScreeningReportSerializer, StructuredReportSerializer
from .eye_health import (
    build_complete_pdf, finalize_screening_report, professional_snapshot,
    require_eye_health_authority, screening_snapshot,
)
from .referral_linking import (
    build_report_pdf_url,
    sync_report_to_local_hospital_referral,
)
from .release_control import is_report_released_to_hospital


class StructuredReportRulesMixin:
    @staticmethod
    def _clinical_metadata(data):
        return {
            "clinician_name": data.get("clinician_name"),
            "professional_role": data.get("professional_role"),
            "registration_number": data.get("registration_number"),
            "reason": data.get("takeover_reason", ""),
        }

    @staticmethod
    def _serializer_data(data):
        cleaned = data.copy()
        for key in (
            "expected_version", "clinician_name", "professional_role",
            "registration_number", "takeover_reason", "correction_note",
            "resubmission_note", "idempotency_key", "submitted_version",
        ):
            cleaned.pop(key, None)
        return cleaned

    def _has_any_uploaded_image(self, encounter) -> bool:
        return ImageUpload.objects.filter(encounter=encounter).exists()

    def _validate_report_prerequisites(self, serializer, patient, encounter):
        if not patient or not encounter:
            raise PermissionDenied("Both patient and encounter are required.")

        if encounter.patient_id != patient.id:
            raise PermissionDenied(
                "The selected encounter does not belong to the selected patient."
            )

        consent_status = (patient.consent_status or "").strip().lower()
        if consent_status != "completed":
            raise PermissionDenied(
                "Cannot create or update report until patient consent is completed."
            )

        has_uploaded_image = self._has_any_uploaded_image(encounter)

        report_marked_ungradable = bool(
            serializer.validated_data.get(
                "ungradable", getattr(serializer.instance, "ungradable", False)
            )
        )

        urgency_outcome = (
            serializer.validated_data.get(
                "urgency_outcome", getattr(serializer.instance, "urgency_outcome", "")
            ) or ""
        ).strip().lower()

        report_marked_retake = urgency_outcome == "image_retake"

        allowed_without_image = report_marked_ungradable or report_marked_retake

        if not has_uploaded_image and not allowed_without_image:
            raise PermissionDenied(
                "Cannot create or update report until an image is uploaded. If no usable image is available, mark the report as Ungradable or Image Retake."
            )


    def _apply_encounter_va_defaults(self, serializer, encounter):
        """
        Technician VA is captured on the encounter. The report should inherit it
        by default, while still allowing the optometrist to override the report
        values if clinically appropriate.
        """
        if not encounter:
            return

        data = serializer.validated_data

        if not data.get("left_unaided_va"):
            data["left_unaided_va"] = getattr(encounter, "left_unaided_va", "") or getattr(encounter, "visual_acuity_left", "")

        if not data.get("right_unaided_va"):
            data["right_unaided_va"] = getattr(encounter, "right_unaided_va", "") or getattr(encounter, "visual_acuity_right", "")

        if not data.get("left_corrected_va"):
            data["left_corrected_va"] = getattr(encounter, "left_corrected_pinhole_va", "")

        if not data.get("right_corrected_va"):
            data["right_corrected_va"] = getattr(encounter, "right_corrected_pinhole_va", "")

    def _validate_report_editable(self, report, user):
        editable_statuses = {
            "draft",
            "under_review",
            "returned_to_clinic",
            "ops_rejected",
        }
        if report.report_status not in editable_statuses:
            raise PermissionDenied(
                f"This report cannot be edited while its status is {report.report_status}. "
                "Reports submitted to Ops or already issued are read-only."
            )

    def _validate_report_can_be_clinic_issued(self, report):
        missing_items = []
        if not report.patient_id: missing_items.append("patient")
        if not report.encounter_id: missing_items.append("assessment")
        if not report.review_date: missing_items.append("review date")
        if (report.patient.consent_status or "").strip().lower() != "completed": missing_items.append("patient consent")
        has_uploaded_image = self._has_any_uploaded_image(report.encounter)
        allowed_without_image = bool(report.ungradable) or ((report.urgency_outcome or "").strip().lower() == "image_retake")
        if not has_uploaded_image and not allowed_without_image:
            missing_items.append("uploaded image or valid ungradable/image retake outcome")
        if missing_items:
            raise PermissionDenied("Report cannot be signed and issued yet. Missing/incomplete: " + ", ".join(missing_items) + ".")

    def _validate_report_can_be_submitted_to_ops(self, report):
        missing_items = []

        if not report.patient_id:
            missing_items.append("patient")
        if not report.encounter_id:
            missing_items.append("encounter")
        if not report.review_date:
            missing_items.append("review_date")

        consent_status = (report.patient.consent_status or "").strip().lower()
        if consent_status != "completed":
            missing_items.append("patient consent")

        has_uploaded_image = self._has_any_uploaded_image(report.encounter)
        allowed_without_image = bool(report.ungradable) or (
            (report.urgency_outcome or "").strip().lower() == "image_retake"
        )

        if not has_uploaded_image and not allowed_without_image:
            missing_items.append("uploaded image or valid ungradable/image retake outcome")

        if missing_items:
            raise PermissionDenied(
                f"Report cannot be submitted to Ops yet. Missing/incomplete: {', '.join(missing_items)}."
            )


class StructuredReportListCreateView(
    StructuredReportRulesMixin, generics.ListCreateAPIView
):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).all()

        report_status = self.request.query_params.get("report_status")
        if report_status:
            queryset = queryset.filter(report_status=report_status)

        user = self.request.user
        if user.is_superuser:
            return queryset

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=self._serializer_data(request.data))
        serializer.is_valid(raise_exception=True)
        user = self.request.user
        patient = serializer.validated_data.get("patient")
        encounter = serializer.validated_data.get("encounter")

        if not encounter.includes_diabetic_screening:
            raise PermissionDenied(
                "General ocular assessments use the ocular clinical record, "
                "not the diabetic grading report."
            )

        self._validate_report_prerequisites(serializer, patient, encounter)
        self._apply_encounter_va_defaults(serializer, encounter)

        existing = StructuredReport.objects.filter(encounter=encounter).first()
        if existing:
            return Response(
                {
                    "detail": "A structured report already exists for this encounter. Edit the existing report.",
                    "existing_report": {"id": existing.pk, "report_id": existing.report_id},
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            with transaction.atomic():
                report = serializer.save(
                    report_owner=("clinic" if encounter.workflow_route == "clinic_managed" else "sentinel")
                )
                responsibility, authority, _ = accept_responsibility(
                    user=user, report=report, **self._clinical_metadata(request.data)
                )
                apply_generated_wording(report)
                apply_recall_schedule(report)
                report.save(update_fields=[
                    "generated_clinical_summary", "final_clinical_summary",
                    "recall_due_date", "recall_status", "updated_at",
                ])
                version, _ = create_version_if_changed(
                    report=report, editor=user, responsibility=responsibility, purpose="initial"
                )
                report.lock_version = 1
                report.save(update_fields=["lock_version", "updated_at"])
                event_once(
                    report=report, event_type="created", actor=user,
                    from_status="", to_status=report.report_status,
                    target_version=version, authority_used=authority,
                    note="Structured report created by clinic.",
                    idempotency_key=f"created:{report.pk}",
                )
        except IntegrityError:
            existing = StructuredReport.objects.filter(encounter=encounter).first()
            if existing:
                return Response(
                    {
                        "detail": "A structured report already exists for this encounter. Edit the existing report.",
                        "existing_report": {"id": existing.pk, "report_id": existing.report_id},
                    }, status=status.HTTP_409_CONFLICT,
                )
            raise
        output = self.get_serializer(report)
        return Response(output.data, status=status.HTTP_201_CREATED)


class StructuredReportDetailView(
    StructuredReportRulesMixin, generics.RetrieveUpdateAPIView
):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        with transaction.atomic():
            report = self.get_queryset().select_for_update().get(pk=kwargs["pk"])
            assert_expected(report, expected_version(request.data))
            self._validate_report_editable(report, request.user)
            serializer = self.get_serializer(
                report, data=self._serializer_data(request.data), partial=partial
            )
            serializer.is_valid(raise_exception=True)
            self._validate_report_prerequisites(serializer, report.patient, report.encounter)
            self._apply_encounter_va_defaults(serializer, report.encounter)
            responsibility = ReportClinicalResponsibility.objects.filter(report=report).first()
            responsibility_changed = False
            if responsibility and responsibility.current_clinician_id == request.user.pk:
                responsibility, authority = require_responsible_clinician(request.user, report)
            else:
                had_responsibility = responsibility is not None
                responsibility, authority, responsibility_changed = accept_responsibility(
                    user=request.user, report=report, **self._clinical_metadata(request.data)
                )
                if responsibility_changed:
                    event_once(
                        report=report,
                        event_type="responsibility_taken_over" if had_responsibility else "responsibility_accepted",
                        actor=request.user,
                        from_status=report.report_status, to_status=report.report_status,
                        source_version=latest_version(report), authority_used=authority,
                        note=("Clinical responsibility taken over; reason recorded."
                              if had_responsibility else "Clinical responsibility explicitly accepted."),
                        correction_note=responsibility.takeover_reason,
                        idempotency_key=f"takeover:{report.lock_version}:{request.user.pk}",
                    )
            meaningful_change = any(
                field in serializer.validated_data
                and getattr(report, field) != serializer.validated_data[field]
                for field in CLINICAL_FIELDS
            )
            if not meaningful_change:
                if responsibility_changed:
                    report.lock_version += 1
                    report.save(update_fields=["lock_version", "updated_at"])
                return Response(self.get_serializer(report).data)
            report = serializer.save()
            apply_generated_wording(report)
            apply_recall_schedule(report)
            report.save(update_fields=[
                "generated_clinical_summary", "final_clinical_summary",
                "recall_due_date", "recall_status", "updated_at",
            ])
            purpose = "returned_correction" if report.report_status in {"returned_to_clinic", "ops_rejected"} else "clinical_edit"
            version, created = create_version_if_changed(
                report=report, editor=request.user, responsibility=responsibility,
                purpose=purpose, correction_note=request.data.get("correction_note", ""),
            )
            if created:
                report.lock_version += 1
                report.save(update_fields=["lock_version", "updated_at"])
        return Response(self.get_serializer(report).data)


class ClinicReportListView(generics.ListAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).prefetch_related("status_events").all()

        user = self.request.user
        if not user.is_superuser:
            user_groups = set(user.groups.values_list("name", flat=True))
            if "ops_admin" not in user_groups:
                org = get_user_organization(user)
                if not org:
                    return StructuredReport.objects.none()
                queryset = queryset.filter(patient__assigned_clinic=org)

        report_status = (self.request.query_params.get("status") or "").strip()
        if report_status and report_status != "all":
            queryset = queryset.filter(report_status=report_status)

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            from django.db import models as db_models
            queryset = queryset.filter(
                db_models.Q(report_id__icontains=search)
                | db_models.Q(patient__patient_id__icontains=search)
                | db_models.Q(patient__first_name__icontains=search)
                | db_models.Q(patient__last_name__icontains=search)
                | db_models.Q(encounter__encounter_id__icontains=search)
            )

        return queryset.order_by("-updated_at")


class EncounterReportListView(generics.ListAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).filter(encounter_id=encounter_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)


class PatientReportListView(generics.ListAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).filter(patient_id=patient_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanSubmitReportToOps])
def submit_report_to_ops(request, pk):
    with transaction.atomic():
        report = StructuredReport.objects.select_for_update().select_related(
            "patient", "patient__assigned_clinic", "encounter",
        ).filter(pk=pk).first()
        if not report:
            raise Http404("Report not found.")
        assert_expected(report, expected_version(request.data))
        responsibility, authority = require_responsible_clinician(request.user, report)
        if report.encounter.workflow_route != "sentinel_managed":
            return Response({"detail": "This is a Clinic Managed assessment. Use Sign and Issue Report instead."}, status=status.HTTP_400_BAD_REQUEST)
        if report.report_status not in {"draft", "under_review", "ops_rejected", "returned_to_clinic"}:
            return Response({"detail": f"This report cannot be submitted from {report.report_status}."}, status=status.HTTP_400_BAD_REQUEST)
        previous_status = report.report_status
        is_resubmission = previous_status in {"ops_rejected", "returned_to_clinic"}
        resubmission_note = (request.data.get("resubmission_note") or "").strip()
        if is_resubmission and not resubmission_note:
            return Response({"detail": "A resubmission note is required."}, status=status.HTTP_400_BAD_REQUEST)
        StructuredReportRulesMixin()._validate_report_can_be_submitted_to_ops(report)
        version = latest_version(report)
        if not version:
            version, _ = create_version_if_changed(
                report=report, editor=request.user, responsibility=responsibility, purpose="initial"
            )
        key = (request.data.get("idempotency_key") or f"submit:{report.lock_version}").strip()[:120]
        prior = ReportStatusEvent.objects.filter(report=report, idempotency_key=key).first()
        if prior:
            return Response(StructuredReportSerializer(report, context={"request": request}).data)
        report.report_status = "submitted_to_ops"
        report.submitted_to_ops_at = timezone.now()
        report.submitted_to_ops_by = request.user
        report.submitted_version = version
        if is_resubmission:
            report.resubmission_count += 1
        report.lock_version += 1
        report.save(update_fields=[
            "report_status", "submitted_to_ops_at", "submitted_to_ops_by",
            "submitted_version", "resubmission_count", "lock_version", "updated_at",
        ])
        event_once(
            report=report, event_type="resubmitted" if is_resubmission else "submitted_to_ops",
            actor=request.user, from_status=previous_status, to_status="submitted_to_ops",
            source_version=version, target_version=version, authority_used=authority,
            note="Report resubmitted to Sentinel Ops." if is_resubmission else "Report submitted to Sentinel Ops.",
            correction_note=resubmission_note, idempotency_key=key,
        )
        local_referral = sync_report_to_local_hospital_referral(report)

    return Response(
        {
            "message": "Report submitted to Sentinel Ops successfully.",
            "report_id": report.report_id,
            "report_pk": report.pk,
            "report_status": report.report_status,
            "lock_version": report.lock_version,
            "submitted_version": report.submitted_version_id,
            "submitted_to_ops_at": report.submitted_to_ops_at,
            "local_hospital_referral_id": local_referral.referral_id if local_referral else "",
        },
        status=status.HTTP_200_OK,
    )



@api_view(["POST"])
@permission_classes([IsAuthenticated, CanSubmitReportToOps])
def clinic_issue_report(request, pk):
    created_pdf = False
    issued_version = None
    try:
        with transaction.atomic():
            report = StructuredReport.objects.select_for_update().select_related(
                "patient", "patient__assigned_clinic", "encounter"
            ).filter(pk=pk).first()
            if not report:
                raise Http404("Report not found.")
            assert_expected(report, expected_version(request.data))
            responsibility, authority = require_responsible_clinician(request.user, report)
            clinic = report.patient.assigned_clinic
            if report.encounter.workflow_route != "clinic_managed":
                return Response({"detail": "Only Clinic Managed assessments can be issued directly by the clinic."}, status=status.HTTP_400_BAD_REQUEST)
            profile, _ = OrganizationProfile.objects.get_or_create(organization=clinic)
            if not profile.can_issue_reports_directly:
                raise PermissionDenied("This clinic is not permitted to issue reports directly.")
            if report.report_status not in {"draft", "under_review", "returned_to_clinic", "ops_rejected"}:
                return Response({"detail": f"Only an editable report can be issued. Current status: {report.report_status}"}, status=status.HTTP_400_BAD_REQUEST)
            signer_name = (request.data.get("signer_name") or responsibility.clinician_name).strip()
            signer_role = (request.data.get("signer_role") or responsibility.professional_role).strip()
            signer_registration_number = (request.data.get("signer_registration_number") or responsibility.registration_number).strip()
            if not signer_name or not signer_role or not signer_registration_number:
                return Response({"detail": "Complete clinician name, role and registration number are required."}, status=status.HTTP_400_BAD_REQUEST)
            StructuredReportRulesMixin()._validate_report_can_be_clinic_issued(report)
            issued_version = latest_version(report)
            if not issued_version:
                issued_version, _ = create_version_if_changed(
                    report=report, editor=request.user, responsibility=responsibility, purpose="initial"
                )
            previous_status = report.report_status
            now = timezone.now()
            report.report_owner = "clinic"
            report.signed_by = request.user
            report.signed_at = now
            report.signer_name = signer_name
            report.signer_role = signer_role
            report.signer_registration_number = signer_registration_number
            report.issued_by = request.user
            report.issued_at = now
            report.sentinel_archive_received_at = now
            report.report_status = "issued"
            report.distribution_status = "awaiting_distribution"
            report.issued_version = issued_version
            report.lock_version += 1
            apply_generated_wording(report)
            apply_recall_schedule(report)
            created_pdf = bind_issued_pdf(report, issued_version, request)
            report.save(update_fields=[
                "report_owner", "signed_by", "signed_at", "signer_name", "signer_role",
                "signer_registration_number", "issued_by", "issued_at",
                "sentinel_archive_received_at", "report_status", "distribution_status",
                "issued_version", "lock_version", "generated_clinical_summary",
                "final_clinical_summary", "recall_due_date", "recall_status", "updated_at",
            ])

            referral = getattr(report.encounter, "hospital_referral", None)
            if referral:
                referral.report = report
                referral.report_ready = False
                referral.referral_status = "report_issued"
                referral.save(update_fields=[
                    "report", "report_ready", "referral_status", "updated_at",
                ])
            else:
                from finance.services import recognize_service_partner_earning
                financial_record = getattr(report.encounter, "financial_record", None)
                if financial_record:
                    recognize_service_partner_earning(financial_record, trigger_source="clinic_report_issue")
            event_once(report=report, event_type="clinic_signed", actor=request.user,
                       from_status=previous_status, to_status="issued", source_version=issued_version,
                       target_version=issued_version, authority_used=authority,
                       note=f"Report electronically signed by {signer_name}.",
                       idempotency_key=f"clinic-issue:{report.lock_version}")
            event_once(report=report, event_type="clinic_issued", actor=request.user,
                       from_status=previous_status, to_status="issued", source_version=issued_version,
                       target_version=issued_version, authority_used=authority,
                       note="Clinic Managed report issued directly by the clinic. Sentinel retained a read-only audit copy.")
            event_once(report=report, event_type="queued_for_distribution", actor=request.user,
                       from_status="issued", to_status="issued", source_version=issued_version,
                       target_version=issued_version, authority_used=authority,
                       note="Issued report queued for Sentinel distribution.")
    except Exception:
        if created_pdf:
            delete_bound_pdf(issued_version)
        raise
    return Response({"message": "Report signed and issued successfully.", "report": StructuredReportSerializer(report, context={"request": request}).data, "report_status": report.report_status, "issued_at": report.issued_at, "report_pdf_url": build_report_pdf_url(request, report)}, status=status.HTTP_200_OK)

@api_view(["POST"])
@permission_classes([IsAuthenticated, CanReviewOpsReports])
def approve_report_by_ops(request, pk):
    # Compatibility wrapper. The canonical transition lives under /api/ops/.
    from ops.views import OpsReportApproveView
    return OpsReportApproveView().post(request, pk)


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanReviewOpsReports])
def reject_report_by_ops(request, pk):
    # Compatibility wrapper. The canonical transition lives under /api/ops/.
    from ops.views import OpsReportRejectView
    return OpsReportRejectView().post(request, pk)


class StructuredReportPDFView(APIView):
    permission_classes = [IsAuthenticated, CanManageReports]

    def get(self, request, pk):
        from .pdf_renderer import ReportPDFRenderer, normalise_report_format

        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
        ).prefetch_related(
            "hospital_referrals",
            "status_events",
            "encounter__image_uploads",
            "encounter__ocular_investigations",
        )

        try:
            report = queryset.get(pk=pk)
        except StructuredReport.DoesNotExist:
            raise Http404("Report not found.")

        user = request.user
        if not user.is_superuser:
            user_groups = set(user.groups.values_list("name", flat=True))
            if "ops_admin" not in user_groups:
                org = get_user_organization(user)
                if not org:
                    raise PermissionDenied("You cannot access this report.")

                hospital_referral = report.hospital_referrals.filter(
                    source_hospital=org,
                ).first()
                is_hospital_user = getattr(org, "organization_type", "") == "hospital"
                is_hospital_match = (
                    is_hospital_user
                    and is_report_released_to_hospital(report, hospital_referral)
                )
                is_clinic_match = (
                    not is_hospital_user
                    and report.patient.assigned_clinic_id == org.id
                )

                if not is_clinic_match and not is_hospital_match:
                    raise PermissionDenied("You cannot access this report.")

                if is_hospital_match:
                    now = timezone.now()
                    if not report.hospital_viewed_at:
                        report.hospital_viewed_at = now
                    report.hospital_downloaded_at = now
                    report.save(
                        update_fields=[
                            "hospital_viewed_at",
                            "hospital_downloaded_at",
                            "updated_at",
                        ]
                    )
                    ReportStatusEvent.objects.create(
                        report=report,
                        event_type="hospital_downloaded",
                        from_status=report.report_status,
                        to_status=report.report_status,
                        note="Hospital opened/downloaded the issued report PDF.",
                        actor=request.user,
                    )

        report_format = normalise_report_format(
            request.query_params.get("report_format")
        )

        # Hospitals may open released hospital, clinician, and
        # patient-friendly presentations only. Ops/Audit remains internal.
        org = get_user_organization(user) if not user.is_superuser else None
        if org and getattr(org, "organization_type", "") == "hospital":
            if report_format not in {
                "hospital",
                "clinician",
                "patient",
            }:
                report_format = "hospital"

        issued_version = report.issued_version
        if report.report_status == "issued" and issued_version and issued_version.pdf_object_key:
            from uploads.storage import get_private_clinical_storage
            with get_private_clinical_storage().open(issued_version.pdf_object_key, "rb") as source:
                pdf_bytes = source.read()
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'inline; filename="{report.report_id}-issued.pdf"'
            return response

        pdf_bytes = ReportPDFRenderer(
            report=report,
            request=request,
            report_format=report_format,
        ).build()

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{report.report_id}-{report_format}.pdf"'
        )
        return response


class EyeHealthScreeningReportView(APIView):
    permission_classes = [IsAuthenticated]

    def _encounter(self, request, encounter_id):
        from encounters.models import ScreeningEncounter
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related(
                "patient__assigned_clinic", "patient__assigned_branch", "service_branch"
            ), pk=encounter_id,
        )
        if not encounter.includes_eye_health_screening:
            raise PermissionDenied("This encounter does not include eye-health screening.")
        require_eye_health_authority(request.user, encounter)
        return encounter

    def get(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        report = EyeHealthScreeningReport.objects.filter(encounter=encounter).first()
        if not report:
            return Response({"detail": "No eye-health screening report draft exists."}, status=404)
        return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)

    @transaction.atomic
    def post(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        encounter = encounter.__class__.objects.select_for_update().get(pk=encounter.pk)
        report = EyeHealthScreeningReport.objects.select_for_update().filter(encounter=encounter).first()
        _created = report is None
        if report is None:
            report = EyeHealthScreeningReport.objects.create(encounter=encounter)
        if report.status == report.Status.FINALIZED:
            return Response({"detail": "The finalized screening report is immutable."}, status=409)
        supplied_version = request.data.get("expected_version")
        if not _created:
            try:
                supplied_version = int(supplied_version)
            except (TypeError, ValueError):
                return Response({"detail": "The current report version is required."}, status=400)
            if supplied_version != report.lock_version:
                return Response(
                    {"detail": "This screening report changed after it was loaded."},
                    status=status.HTTP_409_CONFLICT,
                )
        payload = request.data.copy()
        payload.pop("expected_version", None)
        serializer = EyeHealthScreeningReportSerializer(
            report, data=payload, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(preview_checksum="", previewed_at=None, lock_version=report.lock_version + 1)
        return Response(serializer.data, status=201 if _created else 200)

    patch = post


class EyeHealthScreeningPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        report = get_object_or_404(
            EyeHealthScreeningReport.objects.select_for_update().select_related(
                "encounter__patient__assigned_clinic", "encounter__patient__assigned_branch",
                "encounter__service_branch",
            ), pk=pk,
        )
        try:
            authority, clinic, branch = require_eye_health_authority(request.user, report.encounter)
            clinician = professional_snapshot(request.user, authority)
            clinician.update({
                "clinic_id": clinic.pk, "clinic_name": clinic.name,
                "branch_id": branch.pk, "branch_name": branch.name,
            })
            snapshot, checksum, _manifest = screening_snapshot(report, clinician)
            pdf, _manifest = build_complete_pdf(report, snapshot)
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=400)
        report.preview_checksum = checksum
        report.previewed_at = timezone.now()
        report.save(update_fields=["preview_checksum", "previewed_at", "updated_at"])
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{report.encounter.encounter_id}-eye-health-preview.pdf"'
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response


class EyeHealthScreeningFinalizeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        report = get_object_or_404(EyeHealthScreeningReport, pk=pk)
        try:
            version = finalize_screening_report(
                report, user=request.user,
                expected_version=int(request.data.get("expected_version")),
                signoff_confirmed=request.data.get("signoff_confirmed") is True,
            )
        except (DjangoValidationError, TypeError, ValueError) as exc:
            detail = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": detail}, status=400)
        report.refresh_from_db()
        return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)


class EyeHealthScreeningCorrectionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        report = get_object_or_404(
            EyeHealthScreeningReport.objects.select_for_update().select_related(
                "encounter__patient__assigned_clinic", "encounter__patient__assigned_branch",
                "encounter__service_branch", "finalized_version",
            ), pk=pk,
        )
        require_eye_health_authority(request.user, report.encounter)
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            return Response({"detail": "A correction reason is required."}, status=400)
        if (
            report.status == report.Status.DRAFT
            and report.correction_source_version_id
            and report.correction_reason == reason
        ):
            return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)
        if report.status != report.Status.FINALIZED or not report.finalized_version:
            return Response({"detail": "Only a finalized report can enter correction."}, status=409)
        report.status = report.Status.DRAFT
        report.correction_reason = reason
        report.correction_source_version = report.finalized_version
        report.preview_checksum = ""
        report.previewed_at = None
        report.lock_version += 1
        report.save(update_fields=[
            "status", "correction_reason", "correction_source_version",
            "preview_checksum", "previewed_at", "lock_version", "updated_at",
        ])
        report.encounter.update_status_from_related_records()
        return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)


class EyeHealthScreeningPDFView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from uploads.access import can_access_clinical_asset
        from uploads.storage import get_private_clinical_storage
        report = get_object_or_404(
            EyeHealthScreeningReport.objects.select_related(
                "encounter__patient__assigned_clinic", "encounter__patient__assigned_branch",
                "encounter__service_branch", "finalized_version",
            ), pk=pk,
        )
        encounter = report.encounter
        organization = encounter.patient.assigned_clinic
        branch = encounter.service_branch or encounter.patient.assigned_branch
        if not can_access_clinical_asset(
            request.user, encounter=encounter, organization=organization, branch=branch
        ):
            raise PermissionDenied("You do not have access to this report.")
        version = report.finalized_version
        if not version or not version.pdf_object_key:
            return Response({"detail": "No finalized screening report is available."}, status=404)
        with get_private_clinical_storage().open(version.pdf_object_key, "rb") as source:
            content = source.read()
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{encounter.encounter_id}-eye-health-report.pdf"'
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response


class CombinedScreeningBundleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        from encounters.models import ScreeningEncounter
        from uploads.access import can_access_clinical_asset
        from uploads.storage import get_private_clinical_storage
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related(
                "patient__assigned_clinic", "patient__assigned_branch", "service_branch",
            ), pk=encounter_id, service_package=ScreeningEncounter.ServicePackage.COMBINED,
        )
        organization = encounter.patient.assigned_clinic
        branch = encounter.service_branch or encounter.patient.assigned_branch
        if not can_access_clinical_asset(
            request.user, encounter=encounter, organization=organization, branch=branch
        ):
            raise PermissionDenied("You do not have access to this report bundle.")
        diabetic = getattr(encounter, "structured_report", None)
        screening = getattr(encounter, "eye_health_report", None)
        if (
            not diabetic or diabetic.report_status != "issued" or not diabetic.issued_version
            or not diabetic.issued_version.pdf_object_key
            or not screening or not screening.finalized_version
            or not screening.finalized_version.pdf_object_key
        ):
            return Response({"detail": "Both finalized report components are required."}, status=409)
        storage = get_private_clinical_storage()
        writer = PdfWriter()
        for key in (
            diabetic.issued_version.pdf_object_key,
            screening.finalized_version.pdf_object_key,
        ):
            with storage.open(key, "rb") as source:
                content = source.read()
            for page in PdfReader(BytesIO(content), strict=True).pages:
                writer.add_page(page)
        output = BytesIO()
        writer.write(output)
        response = HttpResponse(output.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{encounter.encounter_id}-combined-screening-bundle.pdf"'
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response
