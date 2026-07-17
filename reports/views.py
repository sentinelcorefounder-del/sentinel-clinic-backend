from io import BytesIO
import os

from django.conf import settings
from django.core.mail import send_mail
from django.http import Http404, HttpResponse
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from organizations.models import OrganizationProfile
from uploads.models import ImageUpload
from .models import StructuredReport, ReportStatusEvent
from .clinical_wording import apply_generated_wording
from .recall_services import apply_recall_schedule
from .permissions import (
    CanManageReports,
    CanReviewOpsReports,
    CanSubmitReportToOps,
)
from .serializers import StructuredReportSerializer
from .services_basecrow import (
    build_report_pdf_url,
    sync_report_to_basecrow_referral,
    sync_report_to_local_hospital_referral,
)


class StructuredReportRulesMixin:
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
            serializer.validated_data.get("ungradable", False)
        )

        urgency_outcome = (
            serializer.validated_data.get("urgency_outcome") or ""
        ).strip().lower()

        report_marked_retake = urgency_outcome == "image_retake"

        print(
            "REPORT RULE DEBUG:",
            {
                "has_uploaded_image": has_uploaded_image,
                "report_marked_ungradable": report_marked_ungradable,
                "urgency_outcome": urgency_outcome,
                "report_marked_retake": report_marked_retake,
                "validated_data_keys": list(serializer.validated_data.keys()),
            },
        )

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
        if user.is_superuser:
            return

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            return

        editable_statuses = {
            "draft",
            "under_review",
            "signed_off",
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

    def perform_create(self, serializer):
        user = self.request.user
        patient = serializer.validated_data.get("patient")
        encounter = serializer.validated_data.get("encounter")

        self._validate_report_prerequisites(serializer, patient, encounter)
        self._apply_encounter_va_defaults(serializer, encounter)

        if StructuredReport.objects.filter(encounter=encounter).exists():
            raise PermissionDenied(
                "A structured report already exists for this encounter. Open and edit the existing report."
            )

        if not user.is_superuser:
            user_groups = set(user.groups.values_list("name", flat=True))
            if "ops_admin" not in user_groups:
                org = get_user_organization(user)
                if not org:
                    raise PermissionDenied("You are not linked to a clinic organization.")

                if patient.assigned_clinic_id != org.id:
                    raise PermissionDenied(
                        "You cannot create reports for another clinic's patient."
                    )

        report = serializer.save(
            report_owner=(
                "clinic"
                if getattr(encounter, "workflow_route", "") == "clinic_managed"
                else "sentinel"
            )
        )
        apply_generated_wording(report)
        apply_recall_schedule(report)
        report.save(
            update_fields=[
                "generated_clinical_summary",
                "final_clinical_summary",
                "recall_due_date",
                "recall_status",
                "updated_at",
            ]
        )

        ReportStatusEvent.objects.get_or_create(
            report=report,
            event_type="created",
            defaults={
                "from_status": "",
                "to_status": report.report_status,
                "note": "Structured report created by clinic.",
                "actor": user,
            },
        )


class StructuredReportDetailView(
    StructuredReportRulesMixin, generics.RetrieveUpdateDestroyAPIView
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

    def perform_update(self, serializer):
        user = self.request.user
        self._validate_report_editable(serializer.instance, user)

        patient = serializer.validated_data.get("patient", serializer.instance.patient)
        encounter = serializer.validated_data.get(
            "encounter", serializer.instance.encounter
        )

        self._validate_report_prerequisites(serializer, patient, encounter)
        self._apply_encounter_va_defaults(serializer, encounter)

        if user.is_superuser:
            report = serializer.save()
            apply_generated_wording(report)
            apply_recall_schedule(report)
            report.save(
                update_fields=[
                    "generated_clinical_summary",
                    "final_clinical_summary",
                    "recall_due_date",
                    "recall_status",
                    "updated_at",
                ]
            )
            return

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            report = serializer.save()
            apply_generated_wording(report)
            apply_recall_schedule(report)
            report.save(
                update_fields=[
                    "generated_clinical_summary",
                    "final_clinical_summary",
                    "recall_due_date",
                    "recall_status",
                    "updated_at",
                ]
            )
            return

        org = get_user_organization(user)
        if not org:
            raise PermissionDenied("You are not linked to a clinic organization.")

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied(
                "You cannot update reports for another clinic's patient."
            )

        report = serializer.save()
        apply_generated_wording(report)
        apply_recall_schedule(report)
        report.save(
            update_fields=[
                "generated_clinical_summary",
                "final_clinical_summary",
                "recall_due_date",
                "recall_status",
                "updated_at",
            ]
        )


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
    try:
        report = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
        ).get(pk=pk)
    except StructuredReport.DoesNotExist:
        raise Http404("Report not found.")

    user = request.user

    if not user.is_superuser:
        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" not in user_groups:
            org = get_user_organization(user)
            if not org or report.patient.assigned_clinic_id != org.id:
                raise PermissionDenied("You cannot submit this report to Ops.")

    if getattr(report.encounter, "workflow_route", "sentinel_managed") != "sentinel_managed":
        return Response({"detail": "This is a Clinic Managed assessment. Use Sign and Issue Report instead of submitting to Sentinel Ops."}, status=status.HTTP_400_BAD_REQUEST)

    if report.report_status not in {"draft", "under_review", "signed_off", "ops_rejected", "returned_to_clinic"}:
        return Response(
            {
                "detail": f"Only draft, under_review, signed_off, ops_rejected, or returned_to_clinic reports can be submitted to Ops. Current status: {report.report_status}"
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    mixin = StructuredReportRulesMixin()
    mixin._validate_report_can_be_submitted_to_ops(report)

    # Clinic submission should enter the Sentinel Ops review queue.
    # Ops will later approve/issue or reject the report.
    previous_status = report.report_status
    is_resubmission = previous_status in {"ops_rejected", "returned_to_clinic"}

    report.report_status = "submitted_to_ops"
    report.submitted_to_ops_at = timezone.now()
    report.return_reason = ""
    if is_resubmission:
        report.resubmission_count += 1
    report.submitted_to_ops_by = user
    report.save(
        update_fields=[
            "report_status",
            "submitted_to_ops_at",
            "submitted_to_ops_by",
            "return_reason",
            "resubmission_count",
            "updated_at",
        ]
    )

    ReportStatusEvent.objects.create(
        report=report,
        event_type="resubmitted" if is_resubmission else "submitted_to_ops",
        from_status=previous_status,
        to_status="submitted_to_ops",
        note="Report resubmitted to Sentinel Ops." if is_resubmission else "Report submitted to Sentinel Ops.",
        actor=user,
    )

    local_referral = sync_report_to_local_hospital_referral(report)

    basecrow_sync = None
    basecrow_sync_error = ""
    try:
        basecrow_sync = sync_report_to_basecrow_referral(report, request=request)
    except Exception as exc:
        # Do not block clinical submission if Basecrow is temporarily unavailable.
        # Return the error so it is visible during testing/logging.
        basecrow_sync_error = str(exc)
        print("Basecrow referral report sync failed:", exc)

    return Response(
        {
            "message": "Report submitted to Sentinel Ops successfully.",
            "report_id": report.report_id,
            "report_pk": report.pk,
            "report_status": report.report_status,
            "submitted_to_ops_at": report.submitted_to_ops_at,
            "report_pdf_url": build_report_pdf_url(request, report),
            "local_hospital_referral_id": local_referral.referral_id if local_referral else "",
            "basecrow_sync": basecrow_sync,
            "basecrow_sync_error": basecrow_sync_error,
        },
        status=status.HTTP_200_OK,
    )



@api_view(["POST"])
@permission_classes([IsAuthenticated, CanSubmitReportToOps])
def clinic_issue_report(request, pk):
    try:
        report = StructuredReport.objects.select_related("patient", "patient__assigned_clinic", "encounter").get(pk=pk)
    except StructuredReport.DoesNotExist:
        raise Http404("Report not found.")

    user = request.user
    clinic = report.patient.assigned_clinic
    if getattr(report.encounter, "workflow_route", "") != "clinic_managed":
        return Response({"detail": "Only Clinic Managed assessments can be issued directly by the clinic."}, status=status.HTTP_400_BAD_REQUEST)
    if not clinic:
        raise PermissionDenied("This report is not linked to an assigned clinic.")
    if not user.is_superuser:
        groups=set(user.groups.values_list("name", flat=True))
        if "ops_admin" in groups:
            raise PermissionDenied("Sentinel Ops has read-only access to Clinic Managed reports.")
        org=get_user_organization(user)
        if not org or org.id != clinic.id:
            raise PermissionDenied("You cannot sign or issue another clinic's report.")

    profile,_=OrganizationProfile.objects.get_or_create(organization=clinic)
    if not profile.can_issue_reports_directly:
        raise PermissionDenied("This clinic is not permitted to issue reports directly.")
    if report.report_status not in {"draft","under_review","signed_off","returned_to_clinic","ops_rejected"}:
        return Response({"detail": f"Only an editable report can be signed and issued. Current status: {report.report_status}"}, status=status.HTTP_400_BAD_REQUEST)

    signer_name = (
        request.data.get("signer_name")
        or report.signer_name
        or ""
    ).strip()

    signer_role = (
        request.data.get("signer_role")
        or report.signer_role
        or ""
    ).strip()

    signer_registration_number = (
        request.data.get("signer_registration_number")
        or report.signer_registration_number
        or ""
    ).strip()

    if profile.electronic_signature_required and not signer_registration_number:
        return Response({"detail": "Electronic signature is incomplete. Registration number is required."}, status=status.HTTP_400_BAD_REQUEST)

    StructuredReportRulesMixin()._validate_report_can_be_clinic_issued(report)
    previous_status=report.report_status
    now=timezone.now()
    report.report_owner="clinic"
    report.signed_by=user
    report.signed_at=now
    report.signer_name=signer_name
    report.signer_role=signer_role
    report.signer_registration_number=signer_registration_number
    report.issued_by=user
    report.issued_at=now
    report.sentinel_archive_received_at=now
    report.report_status="issued"
    report.return_reason = ""
    report.distribution_status = "awaiting_distribution"
    apply_generated_wording(report)
    apply_recall_schedule(report)
    report.save(update_fields=[
        "report_owner", "signed_by", "signed_at", "signer_name",
        "signer_role", "signer_registration_number", "issued_by",
        "issued_at", "sentinel_archive_received_at", "report_status",
        "return_reason",
        "distribution_status",
        "generated_clinical_summary",
        "final_clinical_summary",
        "recall_due_date",
        "recall_status",
        "updated_at",
    ])

    referral = getattr(report.encounter, "hospital_referral", None)
    if referral:
        referral.report = report
        referral.report_ready = False
        referral.referral_status = "report_issued"
        referral.save(update_fields=[
            "report", "report_ready", "referral_status", "updated_at",
        ])

    ReportStatusEvent.objects.create(
        report=report,
        event_type="queued_for_distribution",
        from_status="issued",
        to_status="issued",
        note="Issued report queued for Sentinel distribution.",
        actor=user,
    )
    ReportStatusEvent.objects.create(report=report,event_type="clinic_signed",from_status=previous_status,to_status="issued",note=f"Report electronically signed by {signer_name}.",actor=user)
    ReportStatusEvent.objects.create(report=report,event_type="clinic_issued",from_status=previous_status,to_status="issued",note="Clinic Managed report issued directly by the clinic. Sentinel retained a read-only audit copy.",actor=user)
    return Response({"message":"Report signed and issued successfully.","report":StructuredReportSerializer(report,context={"request":request}).data,"report_status":report.report_status,"issued_at":report.issued_at,"report_pdf_url":build_report_pdf_url(request,report)},status=status.HTTP_200_OK)

def _build_manual_payout_email(report):
    patient = report.patient
    clinic = patient.assigned_clinic if patient else None

    patient_name = f"{patient.first_name} {patient.last_name}".strip() if patient else "Unknown Patient"
    clinic_name = clinic.name if clinic and clinic.name else "Unknown Clinic"
    encounter_id = report.encounter.encounter_id if report.encounter else "-"
    payout_outcome = (report.urgency_outcome or "-").replace("_", " ").title()

    subject = f"[Sentinel Manual Payout] Ops approved report {report.report_id}"

    body = f"""
A Sentinel report has been approved by Ops and is ready for manual payout review.

Report ID: {report.report_id}
Internal Report PK: {report.pk}
Patient: {patient_name}
Patient ID: {patient.patient_id if patient else "-"}
Clinic: {clinic_name}
Encounter ID: {encounter_id}
Review Date: {report.review_date}
Outcome: {payout_outcome}
Approved At: {timezone.now().isoformat()}

Please review and process manual payout.

Sentinel
""".strip()

    return subject, body


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanReviewOpsReports])
def approve_report_by_ops(request, pk):
    try:
        report = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
        ).get(pk=pk)
    except StructuredReport.DoesNotExist:
        raise Http404("Report not found.")

    if report.report_status != "submitted_to_ops":
        return Response(
            {
                "detail": f"Only reports submitted to Ops can be approved. Current status: {report.report_status}"
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    report.report_status = "ops_approved"
    report.ops_reviewed_at = timezone.now()
    report.ops_reviewed_by = request.user
    report.ops_review_note = (request.data.get("note") or "").strip()
    report.save(
        update_fields=[
            "report_status",
            "ops_reviewed_at",
            "ops_reviewed_by",
            "ops_review_note",
            "updated_at",
        ]
    )

    subject, body = _build_manual_payout_email(report)

    send_mail(
        subject=subject,
        message=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=["sentinel.core.founder@gmail.com"],
        fail_silently=False,
    )

    report.payout_email_sent_at = timezone.now()
    report.save(update_fields=["payout_email_sent_at", "updated_at"])

    return Response(
        {
            "message": "Report approved by Ops. Manual payout email sent.",
            "report_id": report.report_id,
            "report_status": report.report_status,
            "payout_email_sent_at": report.payout_email_sent_at,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanReviewOpsReports])
def reject_report_by_ops(request, pk):
    try:
        report = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
        ).get(pk=pk)
    except StructuredReport.DoesNotExist:
        raise Http404("Report not found.")

    if report.report_status != "submitted_to_ops":
        return Response(
            {
                "detail": f"Only reports submitted to Ops can be rejected. Current status: {report.report_status}"
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    report.report_status = "ops_rejected"
    report.ops_reviewed_at = timezone.now()
    report.ops_reviewed_by = request.user
    report.ops_review_note = (request.data.get("note") or "").strip()
    report.save(
        update_fields=[
            "report_status",
            "ops_reviewed_at",
            "ops_reviewed_by",
            "ops_review_note",
            "updated_at",
        ]
    )

    return Response(
        {
            "message": "Report rejected by Ops.",
            "report_id": report.report_id,
            "report_status": report.report_status,
            "ops_review_note": report.ops_review_note,
        },
        status=status.HTTP_200_OK,
    )


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

                is_clinic_match = report.patient.assigned_clinic_id == org.id
                is_hospital_match = report.hospital_referrals.filter(
                    source_hospital=org,
                    report_ready=True,
                ).exists()

                if not is_clinic_match and not is_hospital_match:
                    raise PermissionDenied("You cannot access this report.")

                if is_hospital_match:
                    if report.report_status != "issued":
                        raise PermissionDenied(
                            "This report has not been issued to the hospital yet."
                        )

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
                    report.hospital_referrals.filter(
                        source_hospital=org
                    ).update(
                        referral_status="completed",
                        report_ready=True,
                        updated_at=now,
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

