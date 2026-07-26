from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)
from organizations.report_branding import resolve_report_branding


def _text(value, fallback="-"):
    value = str(value or "").strip()
    return escape(value) if value else fallback


def build_ocular_report_pdf(assessment):
    encounter = assessment.encounter
    patient = encounter.patient
    clinic = patient.assigned_clinic
    branding = resolve_report_branding(encounter, clinic=clinic)
    output = BytesIO()
    buffers = []
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportSection", parent=styles["Heading2"], fontSize=12,
        leading=15, textColor=colors.HexColor("#1F4E79"), spaceAfter=5,
    ))
    doc = SimpleDocTemplate(
        output, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Ocular clinical report - {encounter.encounter_id}",
        author=getattr(branding.primary_organization, "name", "") or "Clinical service",
    )
    story = []

    def brand_cell(brand):
        logo = getattr(brand.organization, "logo", None)
        logo_source = logo or brand.logo_path
        try:
            if logo:
                logo.open("rb")
                logo_buffer = BytesIO(logo.read())
                logo.close()
                buffers.append(logo_buffer)
                logo_source = logo_buffer
            if logo_source:
                return Image(logo_source, width=32 * mm, height=16 * mm, kind="proportional")
        except Exception:
            pass
        return Paragraph(f"<b>{_text(brand.name, 'Clinical report')}</b>", styles["Heading2"])

    primary = branding.primary_organization
    contact = " · ".join(filter(None, [
        str(getattr(primary, "address", "") or "").strip(),
        str(getattr(primary, "phone", "") or "").strip(),
        str(getattr(primary, "contact_email", "") or "").strip(),
    ]))
    brand_cells = [brand_cell(brand) for brand in branding.brands]
    brand_width = 85 * mm / max(len(brand_cells), 1)
    header = Table([[
        *brand_cells,
        Paragraph(
            f"<b>{_text(getattr(primary, 'name', ''), 'Clinical service')}</b>"
            + (f"<br/><font size='8'>{_text(contact)}</font>" if contact else ""),
            styles["BodyText"],
        ),
    ]], colWidths=[brand_width] * len(brand_cells) + [87 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([header, Paragraph("Ocular Clinical Report", styles["Title"]), Spacer(1, 5)])

    details = [
        ["Patient", f"{patient.first_name} {patient.last_name}", "Date of birth", patient.date_of_birth],
        ["Patient ID", patient.patient_id, "Assessment date", encounter.encounter_date],
        ["Report reference", encounter.encounter_id, "Status", "Final" if assessment.completed_at else "Draft"],
    ]
    details_table = Table(details, colWidths=[30 * mm, 56 * mm, 30 * mm, 56 * mm])
    details_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F6F8")),
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#CAD2DC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([details_table, Spacer(1, 9)])

    sections = [
        ("Presenting complaint", assessment.presenting_complaint),
        ("Ocular and relevant history", assessment.ocular_history),
        ("Anterior eye findings", assessment.anterior_eye_findings),
        ("Fundus findings", assessment.fundus_findings),
        ("Visual-field interpretation", assessment.visual_field_summary),
        ("Tonometry interpretation", assessment.tonometry_summary),
        ("Clinical impression / diagnosis", assessment.impression),
        ("Management and referral plan", assessment.management_plan),
        ("Outcome", assessment.get_management_outcome_display()),
    ]
    for heading, value in sections:
        if value:
            story.extend([
                Paragraph(heading, styles["ReportSection"]),
                Paragraph(_text(value), styles["BodyText"]),
                Spacer(1, 6),
            ])

    clinician = assessment.completed_by
    clinician_name = (
        clinician.get_full_name() or clinician.username
        if clinician else "Not yet signed"
    )
    story.extend([
        Spacer(1, 6),
        Paragraph("Clinical sign-off", styles["ReportSection"]),
        Paragraph(
            f"<b>{_text(clinician_name)}</b><br/>"
            f"{'Electronically completed: ' + _text(assessment.completed_at) if assessment.completed_at else 'Draft report'}",
            styles["BodyText"],
        ),
    ])
    footer = str(getattr(primary, "report_footer_note", "") or "").strip()
    if footer:
        story.extend([Spacer(1, 10), Paragraph(_text(footer), styles["BodyText"])])

    if assessment.report_layout == "with_investigations":
        fundus_ids = list(assessment.selected_fundus_upload_ids or [])
        investigation_ids = list(assessment.selected_ocular_investigation_ids or [])
        fundus = {
            item.id: item for item in encounter.image_uploads.filter(id__in=fundus_ids)
        }
        investigations = {
            item.id: item for item in encounter.ocular_investigations.filter(id__in=investigation_ids)
        }
        items = (
            [("fundus", fundus[item_id]) for item_id in fundus_ids if item_id in fundus]
            + [("investigation", investigations[item_id]) for item_id in investigation_ids if item_id in investigations]
        )
        if items:
            story.extend([PageBreak(), Paragraph("Selected Clinical Investigations", styles["Title"])])
        captions = assessment.attachment_captions or {}
        for kind, item in items:
            file_obj = item.image_file if kind == "fundus" else item.file
            if kind == "fundus":
                title = f"Fundus photograph — {item.get_eye_laterality_display()}"
                caption_key = f"fundus:{item.id}"
            else:
                title = f"{item.get_investigation_type_display()} — {item.get_laterality_display()}"
                caption_key = f"investigation:{item.id}"
            story.extend([Spacer(1, 8), Paragraph(_text(title), styles["ReportSection"])])
            if file_obj and not str(file_obj.name).lower().endswith(".pdf"):
                try:
                    file_obj.open("rb")
                    image_buffer = BytesIO(file_obj.read())
                    file_obj.close()
                    buffers.append(image_buffer)
                    story.append(Image(image_buffer, width=154 * mm, height=85 * mm, kind="proportional"))
                except Exception:
                    story.append(Paragraph("Selected image could not be embedded.", styles["BodyText"]))
            else:
                story.append(Paragraph("Selected PDF investigation (listed but not embedded).", styles["BodyText"]))
            caption = captions.get(caption_key)
            if caption:
                story.append(Paragraph(f"<b>Caption:</b> {_text(caption)}", styles["BodyText"]))

    def draw_page(canvas, document):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#CAD2DC"))
        canvas.line(18 * mm, 13 * mm, width - 18 * mm, 13 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#5B6573"))
        canvas.drawString(
            18 * mm,
            8 * mm,
            f"Confidential clinical document · {encounter.encounter_id}",
        )
        canvas.drawRightString(
            width - 18 * mm,
            8 * mm,
            f"Page {document.page}",
        )
        if branding.powered_by_sentinel:
            canvas.drawCentredString(width / 2, 8 * mm, "Powered by Sentinel")
        if not assessment.completed_at:
            try:
                canvas.setFillAlpha(0.16)
            except Exception:
                pass
            canvas.setFillColor(colors.HexColor("#7A8594"))
            canvas.setFont("Helvetica-Bold", 64)
            canvas.translate(width / 2, height / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "DRAFT")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return output.getvalue()
