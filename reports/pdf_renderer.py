from __future__ import annotations

from io import BytesIO
import os

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

REPORT_FORMATS = {"clinician", "patient", "hospital", "ops"}

PRIMARY = colors.HexColor("#1F4E79")
SENTINEL_BLUE = colors.HexColor("#2513B8")
LIGHT_BLUE = colors.HexColor("#EEF4FB")
LIGHT_GREY = colors.HexColor("#F4F6F8")
BORDER = colors.HexColor("#CAD2DC")
TEXT = colors.HexColor("#172033")


def normalise_report_format(value: str | None) -> str:
    value = (value or "clinician").strip().lower()
    return value if value in REPORT_FORMATS else "clinician"


def _display(value, fallback="-") -> str:
    if value is None:
        return fallback
    value = str(value).strip()
    return value or fallback


def _human(value: str | None) -> str:
    return _display(value).replace("_", " ").title()


def _grade_plain_language(dr_grade: str, mac_grade: str) -> str:
    dr_map = {
        "R0": "No diabetic retinal changes were identified.",
        "R1": "Early background changes related to diabetes were identified.",
        "R2": "More significant diabetic retinal changes were identified and closer review is required.",
        "R3A": "Active proliferative diabetic retinal changes were identified and urgent specialist care is required.",
        "R3S": "Previously treated proliferative diabetic retinal changes were identified.",
        "U": "The retinal image could not be graded reliably.",
        "": "No grade was recorded.",
    }
    mac_map = {
        "M0": "No diabetic macular changes were identified.",
        "M1": "Changes affecting or threatening the macula were identified.",
        "U": "The macula could not be graded reliably.",
        "": "",
    }
    parts = [dr_map.get(dr_grade or "", _display(dr_grade))]
    mac_text = mac_map.get(mac_grade or "", _display(mac_grade, ""))
    if mac_text:
        parts.append(mac_text)
    return " ".join(parts)


class ForegroundWatermarkCanvas(pdf_canvas.Canvas):
    """
    Saves each page state and adds the DRAFT watermark after all Platypus
    content has been drawn, keeping it visible above tables and images.
    """

    report = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)

        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_overlay(page_count)
            super().showPage()

        super().save()

    def _draw_overlay(self, page_count):
        width, height = A4

        if self.report is not None and self.report.report_status != "issued":
            self.saveState()
            try:
                self.setFillAlpha(0.16)
            except Exception:
                pass
            self.setFillColor(colors.HexColor("#7A8594"))
            self.setFont("Helvetica-Bold", 64)
            self.translate(width / 2, height / 2)
            self.rotate(35)
            self.drawCentredString(0, 0, "DRAFT")
            self.restoreState()

        self.saveState()
        self.setStrokeColor(BORDER)
        self.line(18 * mm, 13 * mm, width - 18 * mm, 13 * mm)
        self.setFont("Helvetica", 7)
        self.setFillColor(colors.HexColor("#5B6573"))

        report_id = (
            getattr(self.report, "report_id", "")
            if self.report is not None
            else ""
        )

        self.drawString(
            18 * mm,
            8 * mm,
            f"Confidential clinical document · Report {report_id}",
        )
        self.drawRightString(
            width - 18 * mm,
            8 * mm,
            f"Page {self._pageNumber} of {page_count}",
        )
        self.restoreState()


class ReportPDFRenderer:
    def __init__(self, report, request=None, report_format: str = "clinician"):
        self.report = report
        self.request = request
        self.report_format = normalise_report_format(report_format)
        self.patient = report.patient
        self.encounter = report.encounter
        self.clinic = getattr(self.patient, "assigned_clinic", None)
        self._image_buffers: list[BytesIO] = []

        styles = getSampleStyleSheet()
        self.styles = styles
        self.styles.add(
            ParagraphStyle(
                name="SentinelTitle",
                parent=styles["Title"],
                fontName="Helvetica-Bold",
                fontSize=18,
                leading=22,
                textColor=colors.white,
                alignment=TA_LEFT,
                spaceAfter=0,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="SectionHeading",
                parent=styles["Heading2"],
                fontName="Helvetica-Bold",
                fontSize=12,
                leading=15,
                textColor=PRIMARY,
                spaceBefore=4,
                spaceAfter=6,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="BodySmall",
                parent=styles["BodyText"],
                fontName="Helvetica",
                fontSize=9,
                leading=12,
                textColor=TEXT,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="Body",
                parent=styles["BodyText"],
                fontName="Helvetica",
                fontSize=10,
                leading=14,
                textColor=TEXT,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="PatientSummary",
                parent=styles["BodyText"],
                fontName="Helvetica",
                fontSize=11,
                leading=16,
                textColor=TEXT,
                spaceAfter=7,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="Footer",
                parent=styles["BodyText"],
                fontName="Helvetica",
                fontSize=7,
                leading=9,
                textColor=colors.HexColor("#5B6573"),
                alignment=TA_CENTER,
            )
        )

    def build(self) -> bytes:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=f"{self.report.report_id} {self.report_format.title()} Report",
            author="Sentinel",
        )

        story = self._build_story()
        report = self.report

        class SentinelWatermarkCanvas(ForegroundWatermarkCanvas):
            def __init__(canvas_self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                canvas_self.report = report

        doc.build(
            story,
            canvasmaker=SentinelWatermarkCanvas,
        )

        data = buffer.getvalue()
        buffer.close()
        return data

    def _build_story(self):
        if self.report_format == "patient":
            story = self._patient_story()
        elif self.report_format == "hospital":
            story = self._hospital_story()
        elif self.report_format == "ops":
            story = self._ops_story()
        else:
            story = self._clinician_story()

        if self.report_format in {"patient", "clinician", "hospital", "ops"}:
            story.extend(self._image_page())
        return story

    def _logo_flowable(self):
        logo_path = os.path.join(settings.BASE_DIR, "assets", "sentinel-logo.png")
        if os.path.exists(logo_path):
            return Image(logo_path, width=38 * mm, height=18 * mm, kind="proportional")
        return Paragraph("<b>Sentinel</b>", self.styles["Heading2"])

    def _header(self, title: str, subtitle: str = ""):
        clinic_name = _display(getattr(self.clinic, "name", None), "Sentinel")
        right_text = f"<b>{clinic_name}</b>"
        if subtitle:
            right_text += f"<br/><font size='8'>{subtitle}</font>"

        logo_table = Table(
            [[self._logo_flowable(), Paragraph(right_text, self.styles["BodySmall"])]],
            colWidths=[95 * mm, 77 * mm],
        )
        logo_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )

        title_table = Table(
            [[Paragraph(title, self.styles["SentinelTitle"])]],
            colWidths=[172 * mm],
        )
        title_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PRIMARY),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("BOX", (0, 0), (-1, -1), 0.5, PRIMARY),
                ]
            )
        )

        return [
            logo_table,
            title_table,
            Spacer(1, 7),
            self._meta_table(),
            Spacer(1, 8),
        ]

    def _meta_table(self):
        data = [
            [
                Paragraph("<b>Report ID</b>", self.styles["BodySmall"]),
                Paragraph(_display(self.report.report_id), self.styles["BodySmall"]),
                Paragraph("<b>Assessment date</b>", self.styles["BodySmall"]),
                Paragraph(_display(self.report.review_date), self.styles["BodySmall"]),
            ],
            [
                Paragraph("<b>Status</b>", self.styles["BodySmall"]),
                Paragraph(_human(self.report.report_status), self.styles["BodySmall"]),
                Paragraph("<b>Format</b>", self.styles["BodySmall"]),
                Paragraph(self.report_format.title(), self.styles["BodySmall"]),
            ],
        ]
        table = Table(data, colWidths=[25 * mm, 58 * mm, 29 * mm, 60 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
                    ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return table

    def _section_title(self, text: str):
        return Paragraph(text, self.styles["SectionHeading"])

    def _patient_details_table(self, include_contact=True):
        rows = [
            ["Patient", f"{self.patient.first_name} {self.patient.last_name}", "Patient ID", self.patient.patient_id],
            ["Date of birth", _display(self.patient.date_of_birth), "Sex", _human(self.patient.sex)],
        ]
        if include_contact:
            rows.append(["Phone", _display(self.patient.phone), "Email", _display(self.patient.email)])

        table_data = []
        for row in rows:
            table_data.append(
                [
                    Paragraph(f"<b>{row[0]}</b>", self.styles["BodySmall"]),
                    Paragraph(_display(row[1]), self.styles["BodySmall"]),
                    Paragraph(f"<b>{row[2]}</b>", self.styles["BodySmall"]),
                    Paragraph(_display(row[3]), self.styles["BodySmall"]),
                ]
            )
        table = Table(table_data, colWidths=[28 * mm, 58 * mm, 28 * mm, 58 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE),
                    ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return table

    def _clinical_findings_table(self):
        data = [
            [
                Paragraph("<b>Finding</b>", self.styles["BodySmall"]),
                Paragraph("<b>Left eye</b>", self.styles["BodySmall"]),
                Paragraph("<b>Right eye</b>", self.styles["BodySmall"]),
            ],
            ["Unaided VA", _display(self.report.left_unaided_va), _display(self.report.right_unaided_va)],
            ["Corrected / pinhole VA", _display(self.report.left_corrected_va), _display(self.report.right_corrected_va)],
            ["DR grade", _display(self.report.left_dr_grade), _display(self.report.right_dr_grade)],
            ["Maculopathy", _display(self.report.left_maculopathy_grade), _display(self.report.right_maculopathy_grade)],
        ]
        formatted = []
        for row in data:
            formatted.append(
                [
                    cell if hasattr(cell, "wrap") else Paragraph(_display(cell), self.styles["BodySmall"])
                    for cell in row
                ]
            )
        table = Table(formatted, colWidths=[58 * mm, 57 * mm, 57 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        return table

    def _outcome_table(self):
        data = [
            ["Outcome", _human(self.report.urgency_outcome)],
            ["Ungradable / retake required", "Yes" if self.report.ungradable else "No"],
            ["Follow-up", _display(self.report.next_followup_interval)],
        ]
        table = Table(
            [
                [
                    Paragraph(f"<b>{label}</b>", self.styles["BodySmall"]),
                    Paragraph(_display(value), self.styles["BodySmall"]),
                ]
                for label, value in data
            ],
            colWidths=[55 * mm, 117 * mm],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
                    ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return table

    def _signature_block(self):
        sign_name = _display(self.report.signer_name, "Not yet signed")
        sign_role = _display(self.report.signer_role, "")
        sign_reg = _display(self.report.signer_registration_number, "")
        sign_time = _display(self.report.signed_at or self.report.issued_at, "")

        lines = [f"<b>{sign_name}</b>"]
        if sign_role:
            lines.append(sign_role)
        if sign_reg:
            lines.append(f"Registration: {sign_reg}")
        if sign_time:
            lines.append(f"Electronically signed: {sign_time}")

        table = Table(
            [[Paragraph("<br/>".join(lines), self.styles["BodySmall"])]],
            colWidths=[172 * mm],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE),
                    ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        return table

    def _clinician_story(self):
        story = self._header(
            "Clinician Retinal Assessment Report",
            "Detailed clinical format",
        )
        story += [
            self._section_title("Patient details"),
            self._patient_details_table(),
            Spacer(1, 9),
            self._section_title("Clinical findings"),
            self._clinical_findings_table(),
            Spacer(1, 9),
            self._section_title("Outcome and follow-up"),
            self._outcome_table(),
            Spacer(1, 9),
            self._section_title("Recommendation"),
            Paragraph(_display(self.report.recommendation), self.styles["Body"]),
            Spacer(1, 7),
            self._section_title("Clinical notes"),
            Paragraph(_display(self.report.notes), self.styles["Body"]),
            Spacer(1, 10),
            self._section_title("Clinical sign-off"),
            self._signature_block(),
        ]
        return story

    def _patient_story(self):
        left_summary = _grade_plain_language(
            self.report.left_dr_grade,
            self.report.left_maculopathy_grade,
        )
        right_summary = _grade_plain_language(
            self.report.right_dr_grade,
            self.report.right_maculopathy_grade,
        )

        urgency_message = {
            "routine_followup": "Please continue with the recommended routine follow-up.",
            "early_review": "An earlier review has been recommended.",
            "urgent_referral": "An urgent referral has been recommended. Please follow the instructions provided by your clinic.",
            "ophthalmology_required": "A specialist ophthalmology assessment is required.",
            "image_retake": "The retinal photographs need to be repeated because they could not be assessed reliably.",
        }.get(self.report.urgency_outcome, "Please follow the recommendation given by your clinic.")

        story = self._header(
            "Your Diabetic Retinal Assessment Report",
            "Patient-friendly format",
        )
        story += [
            self._section_title("Your details"),
            self._patient_details_table(include_contact=False),
            Spacer(1, 10),
            self._section_title("Your assessment result"),
            Paragraph(f"<b>Left eye:</b> {left_summary}", self.styles["PatientSummary"]),
            Paragraph(f"<b>Right eye:</b> {right_summary}", self.styles["PatientSummary"]),
            Spacer(1, 4),
            self._section_title("What happens next"),
            Paragraph(urgency_message, self.styles["PatientSummary"]),
            Paragraph(_display(self.report.recommendation), self.styles["PatientSummary"]),
            Spacer(1, 8),
            self._section_title("Important"),
            Paragraph(
                "This report supports your ongoing eye care but does not replace urgent medical advice. "
                "Seek prompt help if you develop sudden loss of vision, a new curtain or shadow, flashing lights, "
                "or a sudden increase in floaters.",
                self.styles["Body"],
            ),
            Spacer(1, 10),
            self._section_title("Clinical sign-off"),
            self._signature_block(),
        ]
        return story

    def _hospital_story(self):
        referral = self.report.hospital_referrals.select_related(
            "source_hospital", "matched_clinic"
        ).first()
        hospital_name = (
            getattr(getattr(referral, "source_hospital", None), "name", "")
            if referral else ""
        )
        hospital_mrn = getattr(referral, "hospital_mrn", "") if referral else ""
        referral_id = getattr(referral, "referral_id", "") if referral else ""

        story = self._header(
            "Hospital Retinal Assessment Summary",
            "Referral outcome format",
        )
        story += [
            self._section_title("Patient and referral"),
            self._patient_details_table(include_contact=False),
            Spacer(1, 7),
            Table(
                [
                    ["Referring hospital", _display(hospital_name)],
                    ["Hospital MRN", _display(hospital_mrn)],
                    ["Referral ID", _display(referral_id)],
                    ["Assessment provider", _display(getattr(self.clinic, "name", None))],
                ],
                colWidths=[55 * mm, 117 * mm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
                        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                ),
            ),
            Spacer(1, 9),
            self._section_title("Clinical outcome"),
            self._clinical_findings_table(),
            Spacer(1, 8),
            self._outcome_table(),
            Spacer(1, 8),
            self._section_title("Recommended action"),
            Paragraph(_display(self.report.recommendation), self.styles["Body"]),
            Spacer(1, 10),
            self._section_title("Clinical sign-off"),
            self._signature_block(),
        ]
        return story

    def _ops_story(self):
        story = self._header(
            "Sentinel Ops Audit Report",
            "Internal governance format",
        )
        story += [
            self._section_title("Patient details"),
            self._patient_details_table(),
            Spacer(1, 8),
            self._section_title("Clinical findings"),
            self._clinical_findings_table(),
            Spacer(1, 8),
            self._section_title("Workflow"),
        ]

        workflow_rows = [
            ["Report owner", _human(self.report.report_owner)],
            ["Workflow route", _human(getattr(self.encounter, "workflow_route", ""))],
            ["Source type", _human(getattr(self.encounter, "source_type", ""))],
            ["Submitted to Ops", _display(self.report.submitted_to_ops_at)],
            ["Ops reviewed", _display(self.report.ops_reviewed_at)],
            ["Resubmission count", _display(self.report.resubmission_count)],
            ["Issued", _display(self.report.issued_at)],
            ["Archive received", _display(self.report.sentinel_archive_received_at)],
        ]
        story.append(
            Table(
                workflow_rows,
                colWidths=[55 * mm, 117 * mm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
                        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                ),
            )
        )
        story += [
            Spacer(1, 8),
            self._section_title("Review notes"),
            Paragraph(_display(self.report.ops_review_note), self.styles["Body"]),
            Spacer(1, 8),
            self._section_title("Status timeline"),
        ]

        event_rows = [["Event", "From", "To", "Actor", "Date"]]
        for event in self.report.status_events.select_related("actor").all():
            actor = (
                event.actor.get_full_name()
                or getattr(event.actor, "username", "")
                or getattr(event.actor, "email", "")
                if event.actor
                else "System"
            )
            event_rows.append(
                [
                    _human(event.event_type),
                    _human(event.from_status),
                    _human(event.to_status),
                    _display(actor),
                    _display(event.created_at),
                ]
            )

        story.append(
            Table(
                event_rows,
                colWidths=[38 * mm, 29 * mm, 29 * mm, 40 * mm, 36 * mm],
                repeatRows=1,
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                        ("FONTSIZE", (0, 0), (-1, -1), 7),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                ),
            )
        )
        story += [
            Spacer(1, 9),
            self._section_title("Clinical sign-off"),
            self._signature_block(),
        ]
        return story

    def _image_page(self):
        uploads = list(
            self.encounter.image_uploads.all().order_by("eye_laterality", "id")
        )
        if not uploads:
            return []

        selected = {}
        for upload in uploads:
            eye = (upload.eye_laterality or "").strip().lower()
            if eye not in selected:
                selected[eye] = upload

        ordered = []
        for key in ("right", "od", "left", "os"):
            if key in selected and selected[key] not in ordered:
                ordered.append(selected[key])
        for upload in uploads:
            if upload not in ordered:
                ordered.append(upload)
        ordered = ordered[:2]

        story = [
            PageBreak(),
            *self._header("Retinal Photographs", "Images captured for this assessment"),
        ]

        for index, upload in enumerate(ordered):
            file_obj = getattr(upload, "image_file", None)
            image_flowable = None
            if file_obj:
                try:
                    file_obj.open("rb")
                    image_buffer = BytesIO(file_obj.read())
                    file_obj.close()
                    self._image_buffers.append(image_buffer)
                    image_flowable = Image(
                        image_buffer,
                        width=154 * mm,
                        height=70 * mm,
                        kind="proportional",
                    )
                except Exception:
                    image_flowable = None

            eye_label = _human(getattr(upload, "eye_laterality", "")) or f"Image {index + 1}"
            quality = _human(getattr(upload, "image_quality", ""))
            gradable = "Yes" if getattr(upload, "gradable", False) else "No"

            story.append(self._section_title(f"{eye_label} eye"))
            if image_flowable:
                wrapper = Table([[image_flowable]], colWidths=[172 * mm])
                wrapper.setStyle(
                    TableStyle(
                        [
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                            ("TOPPADDING", (0, 0), (-1, -1), 6),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ]
                    )
                )
                story.append(wrapper)
            else:
                story.append(
                    Paragraph(
                        "Image could not be embedded in this PDF.",
                        self.styles["Body"],
                    )
                )

            story.append(
                Table(
                    [
                        [
                            "Image quality",
                            quality,
                            "Gradable",
                            gradable,
                        ]
                    ],
                    colWidths=[32 * mm, 54 * mm, 28 * mm, 58 * mm],
                    style=TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
                            ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                            ("FONTSIZE", (0, 0), (-1, -1), 8),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    ),
                )
            )
            story.append(Spacer(1, 7))

        return story