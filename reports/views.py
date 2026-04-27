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
from uploads.models import ImageUpload
from .models import StructuredReport
from .permissions import (
    CanManageReports,
    CanReviewOpsReports,
    CanSubmitReportToOps,
)
from .serializers import StructuredReportSerializer


class StructuredReportRulesMixin:
    def _has_any_uploaded_image(self, encounter) -> bool:
        return ImageUpload.objects.filter(encounter=encounter).exists()

    def _validate_report_prerequisites(self, serializer, patient, encounter):
        if not patient or not encounter:
            raise PermissionDenied("Both patient and encounter are required.")

        if encounter.patient_id != patient.id:
            raise PermissionDenied("The selected encounter does not belong to the selected patient.")

        consent_status = (patient.consent_status or "").strip().lower()
        if consent_status != "completed":
            raise PermissionDenied("Cannot create or update report until patient care consent is completed.")

        has_uploaded_image = self._has_any_uploaded_image(encounter)
        report_marked_ungradable = bool(serializer.validated_data.get("ungradable", False))
        urgency_outcome = (serializer.validated_data.get("urgency_outcome") or "").strip().lower()
        report_marked_retake = urgency_outcome == "image_retake"

        if not has_uploaded_image and not (report_marked_ungradable or report_marked_retake):
            raise PermissionDenied(
                "Cannot create or update report until an image is uploaded. "
                "If no usable image is available, mark the report as Ungradable or Image Retake."
            )

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
            missing_items.append("patient care consent")

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


class StructuredReportListCreateView(StructuredReportRulesMixin, generics.ListCreateAPIView):
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

        if not user.is_superuser:
            user_groups = set(user.groups.values_list("name", flat=True))
            if "ops_admin" not in user_groups:
                org = get_user_organization(user)
                if not org:
                    raise PermissionDenied("You are not linked to a clinic organization.")
                if patient.assigned_clinic_id != org.id:
                    raise PermissionDenied("You cannot create reports for another clinic's patient.")

        # Create report should automatically become under_review.
        serializer.save(report_status="under_review")


class StructuredReportDetailView(StructuredReportRulesMixin, generics.RetrieveUpdateDestroyAPIView):
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
        patient = serializer.validated_data.get("patient", serializer.instance.patient)
        encounter = serializer.validated_data.get("encounter", serializer.instance.encounter)

        self._validate_report_prerequisites(serializer, patient, encounter)

        if not user.is_superuser:
            user_groups = set(user.groups.values_list("name", flat=True))
            if "ops_admin" not in user_groups:
                org = get_user_organization(user)
                if not org:
                    raise PermissionDenied("You are not linked to a clinic organization.")
                if patient.assigned_clinic_id != org.id:
                    raise PermissionDenied("You cannot update reports for another clinic's patient.")

        serializer.save()


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

    if report.report_status not in {"draft", "under_review", "signed_off", "ops_rejected"}:
        return Response(
            {"detail": f"Only draft, under_review, signed_off, or ops_rejected reports can be submitted. Current status: {report.report_status}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    mixin = StructuredReportRulesMixin()
    mixin._validate_report_can_be_submitted_to_ops(report)

    # User requested: clicking Submit to Ops should make the report issued.
    # We still preserve submitted_to_ops_at/by for audit and downstream ops.
    report.report_status = "issued"
    report.submitted_to_ops_at = timezone.now()
    report.submitted_to_ops_by = user
    report.save(
        update_fields=[
            "report_status",
            "submitted_to_ops_at",
            "submitted_to_ops_by",
            "updated_at",
        ]
    )

    return Response(
        {
            "message": "Report issued and submitted to Ops successfully.",
            "report_id": report.report_id,
            "report_status": report.report_status,
            "submitted_to_ops_at": report.submitted_to_ops_at,
        },
        status=status.HTTP_200_OK,
    )


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

    if report.report_status not in {"submitted_to_ops", "issued"}:
        return Response(
            {"detail": f"Only submitted/issued reports can be approved. Current status: {report.report_status}"},
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

    if report.report_status not in {"submitted_to_ops", "issued"}:
        return Response(
            {"detail": f"Only submitted/issued reports can be rejected. Current status: {report.report_status}"},
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

    def _draw_label_value_pair(self, pdf, x, y, label, value, offset=86, size=10):
        pdf.setFont("Helvetica-Bold", size)
        pdf.drawString(x, y, label)
        pdf.setFont("Helvetica", size)
        pdf.drawString(x + offset, y, str(value or "-"))

    def _draw_section_box(self, pdf, x, y_bottom, w, h):
        pdf.setFillColorRGB(0.96, 0.96, 0.96)
        pdf.roundRect(x, y_bottom, w, h, 6, stroke=1, fill=1)
        pdf.setFillColorRGB(0, 0, 0)

    def _draw_wrapped_text(self, pdf, text, x, y, max_width, line_height=13):
        pdf.setFont("Helvetica", 10)
        for paragraph in (text or "-").splitlines() or ["-"]:
            words = paragraph.split()
            line = ""
            for word in words:
                candidate = f"{line} {word}".strip()
                if pdf.stringWidth(candidate, "Helvetica", 10) <= max_width:
                    line = candidate
                else:
                    pdf.drawString(x, y, line)
                    y -= line_height
                    line = word
            pdf.drawString(x, y, line or "-")
            y -= line_height
        return y

    def get(self, request, pk):
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
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
                if not org or report.patient.assigned_clinic_id != org.id:
                    raise PermissionDenied("You cannot access this report.")

        patient = report.patient
        encounter = report.encounter
        clinic = patient.assigned_clinic

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        left = 40
        right = width - 40
        content_width = right - left
        y = height - 45
        primary_blue = (31 / 255, 78 / 255, 121 / 255)

        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(left, y, "Sentinel")
        pdf.drawRightString(right, y, clinic.name if clinic and clinic.name else "Clinic")
        y -= 30

        pdf.setFillColorRGB(*primary_blue)
        pdf.roundRect(left, y - 24, content_width, 28, 6, fill=1, stroke=0)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.setFont("Helvetica-Bold", 17)
        pdf.drawString(left + 14, y - 15, "Diabetic Retinal Screening Report")
        pdf.setFillColorRGB(0, 0, 0)

        y -= 42
        pdf.setFont("Helvetica", 10)
        pdf.drawString(left, y, f"Report ID: {report.report_id}")
        pdf.drawRightString(right, y, f"Review Date: {report.review_date}")
        y -= 28

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Patient Details")
        y -= 12
        self._draw_section_box(pdf, left, y - 72, content_width, 72)
        self._draw_label_value_pair(pdf, left + 10, y - 18, "Patient ID:", patient.patient_id)
        self._draw_label_value_pair(pdf, left + 280, y - 18, "Sex:", (patient.sex or "-").title(), offset=48)
        self._draw_label_value_pair(pdf, left + 10, y - 36, "Name:", f"{patient.first_name} {patient.last_name}", offset=50)
        self._draw_label_value_pair(pdf, left + 280, y - 36, "DOB:", patient.date_of_birth, offset=48)
        self._draw_label_value_pair(pdf, left + 10, y - 54, "Phone:", patient.phone or "-", offset=50)
        self._draw_label_value_pair(pdf, left + 280, y - 54, "Email:", patient.email or "-", offset=48)
        y -= 100

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Encounter Details")
        y -= 18
        self._draw_label_value_pair(pdf, left, y, "Encounter ID:", encounter.encounter_id)
        self._draw_label_value_pair(pdf, left + 280, y, "Status:", report.report_status.replace("_", " ").title(), offset=54)
        y -= 18
        self._draw_label_value_pair(pdf, left, y, "Clinic:", clinic.name if clinic else "-", offset=50)
        y -= 28

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Clinical Findings by Eye")
        y -= 12
        self._draw_section_box(pdf, left, y - 118, content_width, 118)

        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(left + 10, y - 18, "Left Eye")
        self._draw_label_value_pair(pdf, left + 10, y - 38, "Unaided VA:", report.left_unaided_va or "-", offset=86)
        self._draw_label_value_pair(pdf, left + 10, y - 56, "Corrected VA:", report.left_corrected_va or "-", offset=86)
        self._draw_label_value_pair(pdf, left + 10, y - 74, "DR Grade:", report.left_dr_grade or "-", offset=86)
        self._draw_label_value_pair(pdf, left + 10, y - 92, "Maculopathy:", report.left_maculopathy_grade or "-", offset=86)

        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(left + 280, y - 18, "Right Eye")
        self._draw_label_value_pair(pdf, left + 280, y - 38, "Unaided VA:", report.right_unaided_va or "-", offset=92)
        self._draw_label_value_pair(pdf, left + 280, y - 56, "Corrected VA:", report.right_corrected_va or "-", offset=92)
        self._draw_label_value_pair(pdf, left + 280, y - 74, "DR Grade:", report.right_dr_grade or "-", offset=92)
        self._draw_label_value_pair(pdf, left + 280, y - 92, "Maculopathy:", report.right_maculopathy_grade or "-", offset=92)

        y -= 142

        self._draw_label_value_pair(pdf, left, y, "Ungradable:", "Yes" if report.ungradable else "No")
        self._draw_label_value_pair(pdf, left + 280, y, "Outcome:", (report.urgency_outcome or "-").replace("_", " ").title(), offset=62)
        y -= 18
        self._draw_label_value_pair(pdf, left, y, "Follow-up:", report.next_followup_interval or "-")
        y -= 30

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Recommendation")
        y -= 12
        self._draw_section_box(pdf, left, y - 70, content_width, 70)
        self._draw_wrapped_text(pdf, report.recommendation or "-", left + 10, y - 18, content_width - 20)
        y -= 92

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Notes")
        y -= 12
        self._draw_section_box(pdf, left, y - 55, content_width, 55)
        self._draw_wrapped_text(pdf, report.notes or "-", left + 10, y - 18, content_width - 20)

        pdf.setStrokeColorRGB(0.75, 0.75, 0.75)
        pdf.line(left, 42, right, 42)
        pdf.setFillColorRGB(0.35, 0.35, 0.35)
        pdf.setFont("Helvetica", 9)
        pdf.drawCentredString(width / 2, 28, clinic.name if clinic and clinic.name else "Sentinel Clinic")

        pdf.showPage()
        pdf.save()

        pdf_bytes = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{report.report_id}.pdf"'
        return response
