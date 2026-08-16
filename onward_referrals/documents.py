from io import BytesIO
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _text(value, fallback="Not recorded"):
    value = str(value or "").strip()
    return value or fallback


def _safe(value, fallback="Not recorded"):
    return escape(_text(value, fallback))


def render_onward_referral(version, *, draft=False):
    output = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="CentreSmall", parent=styles["Small"], alignment=TA_CENTER))
    doc = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=18 * mm,
        title=f"Onward ophthalmology referral {version.referral.referral_reference}",
    )
    patient = version.patient_snapshot or {}
    author = version.author_snapshot or {}
    recipient = version.recipient_snapshot or {}
    source = version.clinical_source_snapshot or {}
    brand = version.branding_snapshot or {}
    story = []
    if draft:
        story.extend([
            Paragraph("DRAFT — NOT FINALIZED", styles["Title"]),
            Paragraph("This preview is not a signed referral letter.", styles["CentreSmall"]),
            Spacer(1, 8),
        ])
    story.extend([
        Paragraph(_safe(brand.get("primary_name"), "Clinical service"), styles["Heading2"]),
        Paragraph("ONWARD OPHTHALMOLOGY REFERRAL", styles["Title"]),
        Paragraph(
            f"Confidential clinical document · {_text(version.referral.referral_reference)} · Version {version.version_number}",
            styles["CentreSmall"],
        ),
        Spacer(1, 10),
    ])
    meta = [
        ["Referral reference", version.referral.referral_reference, "Assessment date", patient.get("assessment_date", "")],
        ["Encounter", patient.get("encounter_reference", ""), "Urgency", version.get_urgency_display()],
        ["Recipient", recipient.get("organization_name", "Clinic-managed handoff"), "Department", recipient.get("department", "")],
    ]
    story.append(Table(meta, colWidths=[32*mm, 52*mm, 28*mm, 56*mm], style=TableStyle([
        ("GRID", (0,0), (-1,-1), .4, colors.grey), ("BACKGROUND", (0,0), (-1,-1), colors.whitesmoke),
        ("VALIGN", (0,0), (-1,-1), "TOP"), ("FONTSIZE", (0,0), (-1,-1), 8),
    ])))
    patient_rows = [
        ["Patient", patient.get("name", ""), "Date of birth", patient.get("date_of_birth", "")],
        ["Sex", patient.get("sex", ""), "Sentinel Patient ID", patient.get("sentinel_patient_id", "")],
    ]
    if patient.get("hospital_mrn"):
        patient_rows.append(["Hospital MRN", patient["hospital_mrn"], "Original referral", patient.get("original_referral_reference", "")])
    if patient.get("phone"):
        patient_rows.append(["Verified contact number", patient["phone"], "", ""])
    story.extend([Spacer(1, 10), Paragraph("Patient identifiers", styles["Heading3"]), Table(
        patient_rows, colWidths=[32*mm, 52*mm, 28*mm, 56*mm],
        style=TableStyle([("GRID",(0,0),(-1,-1),.4,colors.grey),("VALIGN",(0,0),(-1,-1),"TOP"),("FONTSIZE",(0,0),(-1,-1),8)])
    )])
    clinical = [
        ("Reason for referral", version.referral_reason),
        ("Requested specialist action", version.requested_specialist_action),
        ("Relevant complaint and history", version.relevant_history),
        ("Visual acuity", source.get("visual_acuity", "")),
        ("Intraocular pressure", source.get("iop", "")),
        ("Dilation", source.get("dilation", "")),
        ("Pertinent findings", version.pertinent_findings),
        ("Professional impression", version.professional_impression),
        ("Management already provided", version.management_provided),
    ]
    story.extend([Spacer(1, 10), Paragraph("Clinical referral information", styles["Heading3"]), Table(
        [[Paragraph(f"<b>{label}</b>", styles["Small"]), Paragraph(_safe(value), styles["Small"])] for label, value in clinical],
        colWidths=[48*mm, 120*mm], style=TableStyle([("GRID",(0,0),(-1,-1),.4,colors.grey),("VALIGN",(0,0),(-1,-1),"TOP"),("BACKGROUND",(0,0),(0,-1),colors.whitesmoke)])
    )])
    if version.urgency == "emergency":
        story.extend([Spacer(1, 9), Paragraph(
            "<b>EMERGENCY:</b> Creating, downloading or sharing this letter is not a substitute for immediate emergency escalation. It does not guarantee receipt, acceptance, an appointment or treatment.",
            styles["BodyText"],
        ), Paragraph(
            f"Escalation action: {_safe(version.emergency_escalation_method)} — {_safe(version.emergency_escalation_note)}",
            styles["Small"],
        )])
    signed = version.finalized_at or "Not finalized"
    story.extend([
        Spacer(1, 12), Paragraph("Electronic signature", styles["Heading3"]),
        Paragraph(
            f"<b>{_safe(author.get('name'))}</b><br/>{_safe(author.get('role'))}<br/>Registration: {_safe(author.get('registration_number'))}<br/>Signed: {_safe(signed)}",
            styles["BodyText"],
        ), Spacer(1, 10),
        Paragraph(
            f"Generated {_text(version.pdf_generated_at or 'at finalization')} · Confidential · {version.referral.referral_reference} · Version {version.version_number}",
            styles["CentreSmall"],
        ),
    ])
    doc.build(story)
    return output.getvalue()
