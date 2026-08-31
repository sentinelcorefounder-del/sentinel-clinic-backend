import hashlib
import json
from html import escape
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from encounters.clinical_assets import open_ocular_investigation
from organizations.report_branding import resolve_report_branding
from uploads.clinical_assets import open_image_upload
from uploads.storage import get_private_clinical_storage
from users.clinical_authority import exact_clinical_authority
from rest_framework.exceptions import PermissionDenied

from .models import EyeHealthScreeningReport, EyeHealthScreeningReportVersion


LIMITATION = (
    "This was a targeted eye-health screening and not a comprehensive eye examination. "
    "It did not include refraction or a complete assessment of the front of the eye. "
    "Normal screening findings do not exclude every eye condition."
)
EDITABLE_FIELDS = (
    "outcome", "selected_advice", "advice", "right_visual_field_result",
    "left_visual_field_result", "right_fundus_result", "left_fundus_result",
    "selected_fundus_upload_ids", "selected_visual_field_investigation_ids",
)


def require_eye_health_authority(user, encounter):
    authority = exact_clinical_authority(user)
    if not authority:
        raise PermissionDenied("Exact optometrist or qualified reviewer authority is required.")
    clinic = encounter.patient.assigned_clinic
    user_clinic = getattr(getattr(user, "organization_link", None), "organization", None)
    if not clinic or not user_clinic or user_clinic.pk != clinic.pk:
        raise PermissionDenied("The clinician is outside the performing clinic.")
    branch = encounter.service_branch or encounter.patient.assigned_branch
    if not branch or not user.branch_access.filter(branch__organization=clinic).filter(
        Q(branch=branch) | Q(has_all_branch_access=True)
    ).exists():
        raise PermissionDenied("The clinician does not have access to the encounter branch.")
    return authority, clinic, branch


def professional_snapshot(user, authority):
    profile = getattr(user, "clinical_professional_profile", None)
    if not profile or not profile.is_verified:
        raise ValidationError("A verified clinical professional profile is required for sign-off.")
    values = {
        "user_id": user.pk,
        "display_name": profile.display_name.strip(),
        "professional_role": profile.professional_role.strip(),
        "registration_number": profile.registration_number.strip(),
        "qualifications": profile.qualifications.strip(),
        "authority_used": authority,
    }
    if not all(values[key] for key in ("display_name", "professional_role", "registration_number")):
        raise ValidationError("The verified professional profile is incomplete.")
    return values


def _attachment_manifest(report):
    encounter = report.encounter
    fundus_ids = list(report.selected_fundus_upload_ids or [])
    visual_field_ids = list(report.selected_visual_field_investigation_ids or [])
    if len(fundus_ids) > 2:
        raise ValidationError("Select no more than one fundus image per eye.")
    if len(visual_field_ids) > 10:
        raise ValidationError("Select no more than ten visual-field PDF attachments.")
    fundus = {item.pk: item for item in encounter.image_uploads.filter(pk__in=fundus_ids)}
    if len(fundus) != len(set(fundus_ids)):
        raise ValidationError("Every selected fundus image must belong to this encounter.")
    selected_lateralities = [fundus[item_id].eye_laterality for item_id in fundus_ids]
    if len(selected_lateralities) != len(set(selected_lateralities)):
        raise ValidationError("Select no more than one fundus image for each eye.")
    investigations = {
        item.pk: item for item in encounter.ocular_investigations.filter(
            pk__in=visual_field_ids, investigation_type="visual_field"
        )
    }
    if len(investigations) != len(set(visual_field_ids)):
        raise ValidationError("Every selected visual-field PDF must belong to this encounter.")
    manifest = []
    for item_id in fundus_ids:
        item = fundus[item_id]
        with open_image_upload(item, "rb") as source:
            fundus_checksum = hashlib.sha256(source.read()).hexdigest()
        if item.content_sha256 and item.content_sha256 != fundus_checksum:
            raise ValidationError("A selected fundus image failed its integrity check.")
        manifest.append({
            "kind": "fundus", "id": item.pk, "laterality": item.eye_laterality,
            "checksum_sha256": fundus_checksum,
        })
    total_pdf_pages = 0
    for item_id in visual_field_ids:
        item = investigations[item_id]
        with open_ocular_investigation(item, "rb") as source:
            content = source.read()
        if not content.startswith(b"%PDF-"):
            raise ValidationError(f"Selected visual-field attachment {item.investigation_id} is not a readable PDF.")
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            page_count = len(reader.pages)
            if page_count < 1:
                raise ValueError("empty PDF")
        except Exception as exc:
            raise ValidationError(
                f"Selected visual-field attachment {item.investigation_id} is unreadable."
            ) from exc
        total_pdf_pages += page_count
        if total_pdf_pages > 100:
            raise ValidationError("Selected visual-field attachments exceed the 100-page report limit.")
        content_checksum = hashlib.sha256(content).hexdigest()
        if item.content_sha256 and item.content_sha256 != content_checksum:
            raise ValidationError(
                f"Selected visual-field attachment {item.investigation_id} failed its integrity check."
            )
        manifest.append({
            "kind": "visual_field_pdf", "id": item.pk, "laterality": item.laterality,
            "checksum_sha256": content_checksum,
            "page_count": page_count,
        })
    return manifest, fundus, investigations


def screening_snapshot(report, clinician):
    encounter = report.encounter
    manifest, _fundus, _investigations = _attachment_manifest(report)
    location = encounter.assessment_location_snapshot or {}
    snapshot = {
        "schema_version": 1,
        "service_package": encounter.service_package,
        "encounter_reference": encounter.encounter_id,
        "assessment_date": encounter.encounter_date.isoformat(),
        "assessment_location": location,
        "tests": {
            "visual_acuity": True,
            "iop": bool(encounter.iop_before_dilation_left or encounter.iop_before_dilation_right or encounter.iop_after_dilation_left or encounter.iop_after_dilation_right),
            "visual_fields": bool(report.right_visual_field_result or report.left_visual_field_result or report.selected_visual_field_investigation_ids),
            "fundus": bool(report.right_fundus_result or report.left_fundus_result or report.selected_fundus_upload_ids),
        },
        "right": {
            "visual_acuity": encounter.right_corrected_pinhole_va or encounter.right_unaided_va or encounter.visual_acuity_right,
            "iop": encounter.iop_before_dilation_right or encounter.iop_after_dilation_right,
            "visual_field": report.right_visual_field_result,
            "fundus": report.right_fundus_result,
        },
        "left": {
            "visual_acuity": encounter.left_corrected_pinhole_va or encounter.left_unaided_va or encounter.visual_acuity_left,
            "iop": encounter.iop_before_dilation_left or encounter.iop_after_dilation_left,
            "visual_field": report.left_visual_field_result,
            "fundus": report.left_fundus_result,
        },
        "outcome": report.outcome,
        "outcome_display": report.get_outcome_display() if report.outcome else "",
        "selected_advice": list(report.selected_advice or []),
        "advice": report.advice,
        "limitation": LIMITATION,
        "clinician": clinician,
        "attachments": manifest,
    }
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return snapshot, hashlib.sha256(encoded).hexdigest(), manifest


def _display(value):
    return escape(str(value or "Not recorded")).replace("\n", "<br/>")


def _branding(encounter):
    clinic = encounter.patient.assigned_clinic
    branding = resolve_report_branding(encounter, clinic=clinic)
    primary = branding.primary_organization
    footer = str(getattr(primary, "report_footer_note", "") or "").strip()
    return branding, primary, footer


def build_screening_pdf(report, snapshot):
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("EyeTitle", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#12395b"), fontSize=18)
    small = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=8.5, leading=11)
    branding, primary, footer = _branding(report.encounter)
    patient = report.encounter.patient
    location = snapshot.get("assessment_location") or {}
    location_text = " · ".join(value for value in (location.get("site_name"), location.get("address")) if value) or "Not recorded"
    tests = [name.replace("_", " ").title() for name, included in snapshot["tests"].items() if included]
    buffers = []

    def brand_cell(brand):
        logo = getattr(brand.organization, "logo", None)
        source = logo or brand.logo_path
        try:
            if logo:
                logo.open("rb")
                buffer = BytesIO(logo.read())
                logo.close()
                buffers.append(buffer)
                source = buffer
            if source:
                return Image(source, width=32 * mm, height=16 * mm, kind="proportional")
        except Exception:
            pass
        return Paragraph(f"<b>{_display(brand.name)}</b>", styles["Heading2"])

    brand_cells = [brand_cell(brand) for brand in branding.brands]
    contact = " · ".join(filter(None, [
        str(getattr(primary, "address", "") or "").strip(),
        str(getattr(primary, "phone", "") or "").strip(),
        str(getattr(primary, "contact_email", "") or "").strip(),
    ]))
    header = Table([[
        *brand_cells,
        Paragraph(
            f"<b>{_display(getattr(primary, 'name', ''))}</b>"
            + (f"<br/><font size='8'>{_display(contact)}</font>" if contact else ""),
            styles["BodyText"],
        ),
    ]], colWidths=[85 * mm / max(len(brand_cells), 1)] * len(brand_cells) + [87 * mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story = [
        header,
        Paragraph("Eye Health Screening Report", title), Spacer(1, 5*mm),
        Table([
            ["Patient", _display(f"{patient.first_name} {patient.last_name}".strip()), "Reference", _display(patient.patient_id)],
            ["Assessment", _display(report.encounter.encounter_id), "Date", _display(snapshot["assessment_date"])],
            ["Assessment location", _display(location_text), "Tests included", _display(", ".join(tests))],
        ], colWidths=[32*mm, 55*mm, 32*mm, 55*mm], style=TableStyle([
            ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#b8c6d1")),
            ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#eef4f8")),
            ("BACKGROUND", (2,0), (2,-1), colors.HexColor("#eef4f8")),
            ("VALIGN", (0,0), (-1,-1), "TOP"), ("FONTSIZE", (0,0), (-1,-1), 8),
        ])), Spacer(1, 5*mm),
        Paragraph("Screening results", styles["Heading2"]),
        Table([
            ["Test", "Right eye", "Left eye"],
            ["Visual acuity", _display(snapshot["right"]["visual_acuity"]), _display(snapshot["left"]["visual_acuity"])],
            ["IOP", _display(snapshot["right"]["iop"]), _display(snapshot["left"]["iop"])],
            ["Visual fields", _display(snapshot["right"]["visual_field"]), _display(snapshot["left"]["visual_field"])],
            ["Fundus assessment", _display(snapshot["right"]["fundus"]), _display(snapshot["left"]["fundus"])],
        ], colWidths=[42*mm, 64*mm, 64*mm], style=TableStyle([
            ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#b8c6d1")),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#12395b")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ])), Spacer(1, 4*mm),
        Paragraph("Clinician-confirmed outcome", styles["Heading2"]),
        Paragraph(_display(snapshot["outcome_display"]), styles["BodyText"]),
        Paragraph("Advice", styles["Heading2"]), Paragraph(_display(snapshot["advice"]), styles["BodyText"]),
        Spacer(1, 3*mm), Paragraph(_display(snapshot["limitation"]), small), Spacer(1, 4*mm),
        Paragraph("Clinician sign-off", styles["Heading2"]),
        Paragraph(
            _display(
                f"{snapshot['clinician']['display_name']} · {snapshot['clinician']['professional_role']} · "
                f"Registration {snapshot['clinician']['registration_number']}"
                + (f" · {snapshot['clinician']['qualifications']}" if snapshot['clinician'].get('qualifications') else "")
            ),
            styles["BodyText"],
        ),
    ]
    if footer:
        story.extend([Spacer(1, 4*mm), Paragraph(_display(footer), small)])
    doc.build(story)
    return output.getvalue()


def _fundus_page(report, fundus):
    ids = list(report.selected_fundus_upload_ids or [])
    if not ids:
        return b""
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    story = [Paragraph("Clinician-selected fundus images", styles["Heading1"]), Spacer(1, 3*mm)]
    buffers = []
    for item_id in ids:
        item = fundus[item_id]
        with open_image_upload(item, "rb") as source:
            buffer = BytesIO(source.read())
        buffers.append(buffer)
        story.extend([
            Paragraph(f"{item.get_eye_laterality_display()} eye", styles["Heading2"]),
            Image(buffer, width=165*mm, height=82*mm, kind="proportional"), Spacer(1, 3*mm),
        ])
    doc.build(story)
    return output.getvalue()


def build_complete_pdf(report, snapshot):
    manifest, fundus, investigations = _attachment_manifest(report)
    writer = PdfWriter()
    for page in PdfReader(BytesIO(build_screening_pdf(report, snapshot))).pages:
        writer.add_page(page)
    fundus_pdf = _fundus_page(report, fundus)
    if fundus_pdf:
        for page in PdfReader(BytesIO(fundus_pdf)).pages:
            writer.add_page(page)
    for item_id in report.selected_visual_field_investigation_ids or []:
        with open_ocular_investigation(investigations[item_id], "rb") as source:
            attachment = source.read()
        for page in PdfReader(BytesIO(attachment), strict=True).pages:
            writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    return output.getvalue(), manifest


@transaction.atomic
def finalize_screening_report(report, *, user, expected_version, signoff_confirmed):
    report = EyeHealthScreeningReport.objects.select_for_update().select_related(
        "encounter__patient__assigned_clinic", "encounter__service_branch"
    ).get(pk=report.pk)
    authority, clinic, branch = require_eye_health_authority(user, report.encounter)
    if report.status == report.Status.FINALIZED:
        return report.finalized_version
    if report.lock_version != expected_version:
        raise ValidationError("This screening report changed after it was loaded.")
    if not signoff_confirmed:
        raise ValidationError("Explicit clinician sign-off confirmation is required.")
    if not report.outcome or not report.advice.strip():
        raise ValidationError("A clinician-confirmed outcome and final advice are required.")
    clinician = professional_snapshot(user, authority)
    clinician.update({"clinic_id": clinic.pk, "clinic_name": clinic.name, "branch_id": branch.pk, "branch_name": branch.name})
    snapshot, checksum, manifest = screening_snapshot(report, clinician)
    if not report.preview_checksum or report.preview_checksum != checksum:
        raise ValidationError("Preview the current screening report before finalization.")
    pdf, manifest = build_complete_pdf(report, snapshot)
    version = EyeHealthScreeningReportVersion.objects.create(
        report=report, version_number=report.versions.count() + 1,
        clinical_snapshot=snapshot, checksum_sha256=checksum,
        clinician_snapshot=clinician, attachment_manifest=manifest, editor=user,
        purpose="correction" if report.correction_source_version_id else "initial",
        correction_note=report.correction_reason,
        source_version=report.correction_source_version,
    )
    key = f"clinical-documents/eye-health-reports/encounter-{report.encounter_id}/v{version.version_number}.pdf"
    storage = get_private_clinical_storage()
    pdf_checksum = hashlib.sha256(pdf).hexdigest()
    if storage.exists(key):
        with storage.open(key, "rb") as existing:
            existing_checksum = hashlib.sha256(existing.read()).hexdigest()
        if existing_checksum != pdf_checksum:
            raise ValidationError("A conflicting finalized screening report object already exists.")
    else:
        saved = storage.save(key, ContentFile(pdf))
        if saved != key:
            storage.delete(saved)
            raise ValidationError("The finalized screening report could not be stored safely.")
    EyeHealthScreeningReportVersion.objects.filter(pk=version.pk).update(
        pdf_object_key=key, pdf_checksum_sha256=pdf_checksum, pdf_size=len(pdf)
    )
    report.status = report.Status.FINALIZED
    report.finalized_version = version
    report.correction_reason = ""
    report.correction_source_version = None
    report.lock_version += 1
    report.save(update_fields=[
        "status", "finalized_version", "correction_reason",
        "correction_source_version", "lock_version", "updated_at",
    ])
    report.encounter.update_status_from_related_records()
    return EyeHealthScreeningReportVersion.objects.get(pk=version.pk)
