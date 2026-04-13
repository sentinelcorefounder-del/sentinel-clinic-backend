from io import BytesIO
import os

from django.conf import settings
from django.http import Http404, HttpResponse
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.tenant import get_user_organization
from uploads.models import ImageUpload
from .models import StructuredReport
from .permissions import CanManageReports
from .serializers import StructuredReportSerializer


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
        ).all()

        user = self.request.user
        if user.is_superuser:
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

        if user.is_superuser:
            serializer.save()
            return

        org = get_user_organization(user)
        if not org:
            raise PermissionDenied("You are not linked to a clinic organization.")

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied(
                "You cannot create reports for another clinic's patient."
            )

        serializer.save()


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
        ).all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)

    def perform_update(self, serializer):
        user = self.request.user
        patient = serializer.validated_data.get("patient", serializer.instance.patient)
        encounter = serializer.validated_data.get(
            "encounter", serializer.instance.encounter
        )

        self._validate_report_prerequisites(serializer, patient, encounter)

        if user.is_superuser:
            serializer.save()
            return

        org = get_user_organization(user)
        if not org:
            raise PermissionDenied("You are not linked to a clinic organization.")

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied(
                "You cannot update reports for another clinic's patient."
            )

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
        ).filter(encounter_id=encounter_id)

        user = self.request.user
        if user.is_superuser:
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
        ).filter(patient_id=patient_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)


class StructuredReportPDFView(APIView):
    permission_classes = [IsAuthenticated, CanManageReports]

    def _draw_wrapped_text(
        self,
        pdf: canvas.Canvas,
        text: str,
        x: int,
        y: int,
        max_width: int,
        line_height: int = 14,
        font_name: str = "Helvetica",
        font_size: int = 11,
    ) -> int:
        pdf.setFont(font_name, font_size)
        lines = []

        for paragraph in (text or "-").splitlines() or ["-"]:
            words = paragraph.split()
            if not words:
                lines.append("")
                continue

            current = words[0]
            for word in words[1:]:
                trial = f"{current} {word}"
                if pdf.stringWidth(trial, font_name, font_size) <= max_width:
                    current = trial
                else:
                    lines.append(current)
                    current = word
            lines.append(current)

        for line in lines:
            pdf.drawString(x, y, line)
            y -= line_height

        return y

    def _draw_label_value_pair(
        self,
        pdf: canvas.Canvas,
        x: int,
        y: int,
        label: str,
        value: str,
        label_font: str = "Helvetica-Bold",
        value_font: str = "Helvetica",
        size: int = 11,
        offset: int = 78,
    ):
        pdf.setFont(label_font, size)
        pdf.drawString(x, y, label)
        pdf.setFont(value_font, size)
        pdf.drawString(x + offset, y, value or "-")

    def _draw_section_box(
        self,
        pdf: canvas.Canvas,
        x: int,
        y_bottom: int,
        w: int,
        h: int,
    ):
        pdf.setFillColorRGB(0.96, 0.96, 0.96)
        pdf.roundRect(x, y_bottom, w, h, 6, stroke=1, fill=1)
        pdf.setFillColorRGB(0, 0, 0)

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

        sentinel_logo_path = os.path.join(
            settings.BASE_DIR / "assets", "sentinel-logo.png"
        )

        if os.path.exists(sentinel_logo_path):
            try:
                pdf.drawImage(
                    ImageReader(sentinel_logo_path),
                    left,
                    y - 28,
                    width=90,
                    height=28,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            except Exception:
                pdf.setFont("Helvetica-Bold", 12)
                pdf.drawString(left, y - 5, "Sentinel")
        else:
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawString(left, y - 5, "Sentinel")

        clinic_display_name = clinic.name if clinic and clinic.name else "Clinic"

        if clinic and clinic.logo:
            try:
                pdf.drawImage(
                    ImageReader(clinic.logo.path),
                    right - 90,
                    y - 28,
                    width=90,
                    height=28,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            except Exception:
                pdf.setFont("Helvetica-Bold", 12)
                pdf.drawRightString(right, y - 5, clinic_display_name)
        else:
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawRightString(right, y - 5, clinic_display_name)

        y -= 48

        pdf.setFillColorRGB(*primary_blue)
        pdf.roundRect(left, y - 24, content_width, 28, 6, fill=1, stroke=0)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.setFont("Helvetica-Bold", 17)
        pdf.drawString(left + 14, y - 15, "Diabetic Retinal Screening Report")

        y -= 42
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(left, y, f"Report ID: {report.report_id}")
        pdf.drawRightString(right, y, f"Review Date: {report.review_date}")

        y -= 24

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Patient Details")
        y -= 10

        box_height = 72
        self._draw_section_box(pdf, left, y - box_height, content_width, box_height)

        row1_y = y - 18
        row2_y = y - 36
        row3_y = y - 54

        self._draw_label_value_pair(
            pdf, left + 10, row1_y, "Patient ID:", patient.patient_id
        )
        self._draw_label_value_pair(
            pdf, left + 280, row1_y, "Sex:", (patient.sex or "-").title(), offset=52
        )

        self._draw_label_value_pair(
            pdf,
            left + 10,
            row2_y,
            "Name:",
            f"{patient.first_name} {patient.last_name}",
            offset=48,
        )
        self._draw_label_value_pair(
            pdf, left + 280, row2_y, "DOB:", str(patient.date_of_birth), offset=46
        )

        self._draw_label_value_pair(
            pdf, left + 10, row3_y, "Phone:", patient.phone or "-", offset=52
        )
        self._draw_label_value_pair(
            pdf, left + 280, row3_y, "Email:", patient.email or "-", offset=50
        )

        y -= box_height + 24

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Encounter Details")
        y -= 18

        self._draw_label_value_pair(
            pdf, left, y, "Encounter ID:", encounter.encounter_id, offset=84
        )
        self._draw_label_value_pair(
            pdf,
            left + 280,
            y,
            "Status:",
            report.report_status.replace("_", " ").title(),
            offset=52,
        )
        y -= 18

        clinic_name = clinic.name if clinic else "-"
        self._draw_label_value_pair(pdf, left, y, "Clinic:", clinic_name, offset=42)
        y -= 28

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Clinical Findings")
        y -= 10

        findings_height = 72
        self._draw_section_box(
            pdf, left, y - findings_height, content_width, findings_height
        )

        fy1 = y - 18
        fy2 = y - 36
        fy3 = y - 54

        self._draw_label_value_pair(
            pdf, left + 10, fy1, "DR Grade:", report.dr_grade or "-"
        )
        self._draw_label_value_pair(
            pdf,
            left + 280,
            fy1,
            "Maculopathy:",
            report.maculopathy_grade or "-",
            offset=78,
        )

        self._draw_label_value_pair(
            pdf,
            left + 10,
            fy2,
            "Ungradable:",
            "Yes" if report.ungradable else "No",
        )
        self._draw_label_value_pair(
            pdf,
            left + 280,
            fy2,
            "Outcome:",
            (report.urgency_outcome or "-").replace("_", " ").title(),
            offset=56,
        )

        self._draw_label_value_pair(
            pdf,
            left + 10,
            fy3,
            "Follow-up:",
            report.next_followup_interval or "-",
        )

        y -= findings_height + 24

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Recommendation")
        y -= 10

        rec_box_height = 70
        self._draw_section_box(
            pdf, left, y - rec_box_height, content_width, rec_box_height
        )
        y = self._draw_wrapped_text(
            pdf,
            report.recommendation or "-",
            left + 10,
            y - 18,
            content_width - 20,
            line_height=14,
        )

        rec_bottom = y
        y = min(rec_bottom, (y + 18) - rec_box_height - 18)

        y -= 8

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Notes")
        y -= 10

        notes_box_height = 60
        self._draw_section_box(
            pdf, left, y - notes_box_height, content_width, notes_box_height
        )
        notes_text_y = self._draw_wrapped_text(
            pdf,
            report.notes or "-",
            left + 10,
            y - 18,
            content_width - 20,
            line_height=14,
        )

        notes_bottom = y - notes_box_height
        y = min(notes_text_y, notes_bottom) - 26

        sign_name = (
            clinic.report_signatory_name
            if clinic and clinic.report_signatory_name
            else "Doctor Name"
        )
        sign_title = (
            clinic.report_signatory_title
            if clinic and clinic.report_signatory_title
            else "Doctor / Reviewer"
        )
        sign_odorbn = (
            clinic.report_signatory_odorbn
            if clinic and clinic.report_signatory_odorbn
            else "________________"
        )

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, y, "Authorized Sign-off")
        y -= 24

        pdf.line(left, y, left + 220, y)
        y -= 14

        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(left, y, sign_name)
        y -= 15

        pdf.setFont("Helvetica", 11)
        pdf.drawString(left, y, sign_title)
        y -= 15

        pdf.drawString(left, y, f"ODORBN: {sign_odorbn}")

        footer_parts = []
        if clinic and clinic.name:
            footer_parts.append(clinic.name)
        if clinic and clinic.contact_email:
            footer_parts.append(clinic.contact_email)
        if clinic and getattr(clinic, "phone", ""):
            footer_parts.append(clinic.phone)
        if clinic and getattr(clinic, "address", ""):
            footer_parts.append(clinic.address)

        footer_text = (
            "  |  ".join([part for part in footer_parts if part])
            or "Clinic Information"
        )

        pdf.setStrokeColorRGB(0.75, 0.75, 0.75)
        pdf.line(left, 42, right, 42)
        pdf.setFillColorRGB(0.35, 0.35, 0.35)
        pdf.setFont("Helvetica", 9)
        pdf.drawCentredString(width / 2, 28, footer_text[:150])

        pdf.showPage()
        pdf.save()

        pdf_bytes = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{report.report_id}.pdf"'
        return response