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
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from encounters.clinical_assets import open_ocular_investigation
from organizations.report_branding import resolve_report_branding
from uploads.clinical_assets import open_image_upload
from uploads.storage import get_private_clinical_storage
from users.clinical_authority import exact_clinical_authority
from rest_framework.exceptions import PermissionDenied

from .models import EyeHealthScreeningReport, EyeHealthScreeningReportVersion


LIMITATION = (
    "This was a targeted screening of visual acuity, eye pressure, visual fields and the retina/optic nerve. "
    "It was not a comprehensive eye examination and did not include refraction, slit-lamp examination or a "
    "complete assessment of the cornea, lens and front of the eye. Cataract, corneal conditions and other eye "
    "disorders may therefore not be detected. Screening findings do not confirm or exclude glaucoma. A routine "
    "comprehensive eye examination remains advisable."
)


def limitation_for_tests(tests):
    if all(tests.get(key) for key in ("visual_acuity", "iop", "visual_fields", "fundus")):
        return LIMITATION
    labels = [
        label for key, label in (
            ("visual_acuity", "visual acuity"), ("iop", "eye pressure"),
            ("visual_fields", "visual fields"), ("fundus", "the retina/optic nerve"),
        ) if tests.get(key)
    ]
    performed = ", ".join(labels[:-1]) + (" and " + labels[-1] if len(labels) > 1 else (labels[0] if labels else "limited recorded elements"))
    return (
        f"This was a targeted screening of {performed}. It was not a comprehensive eye examination and did not "
        "include refraction, slit-lamp examination or a complete assessment of the cornea, lens and front of the eye. "
        "Cataract, corneal conditions and other eye disorders may therefore not be detected. Screening findings do "
        "not confirm or exclude glaucoma. A routine comprehensive eye examination remains advisable."
    )
EDITABLE_FIELDS = (
    "outcome", "selected_advice", "advice", "right_visual_field_result",
    "left_visual_field_result", "right_fundus_result", "left_fundus_result",
    "structured_findings", "clinical_summary",
    "selected_fundus_upload_ids", "selected_visual_field_investigation_ids",
)

STRUCTURED_OPTIONS = {
    "fundus_quality": {"good", "mildly_limited", "significantly_limited", "ungradable", ""},
    "optic_disc": {"no_concerning_feature", "symmetrical_cupping", "asymmetrical_cupping", "possible_physiological_cupping", "rim_thinning_notching", "disc_haemorrhage", "other"},
    "retinal_vessels": {"no_concerning_feature", "av_nicking", "vascular_attenuation", "other"},
    "retina_macula": {"no_visible_abnormality", "retinal_haemorrhage", "retinal_exudate", "cotton_wool_spot", "macular_concern", "other"},
    "visual_field_reliability": {"reliable", "reduced_reliability", "unreliable", "unable_to_assess", "not_performed", ""},
    "visual_field_result": {"within_expected_limits", "essentially_full", "localized_reduction", "generalized_reduction", "significant_abnormality", "inconclusive", "not_performed", ""},
    "ght": {"within_normal_limits", "borderline", "outside_normal_limits", "unavailable", ""},
    "iop_interpretation": {"within_expected_range", "raised", "asymmetrical", "unavailable", "other", ""},
    "visual_acuity_interpretation": {"within_expected_range", "reduced_right", "reduced_left", "reduced_both", "asymmetrical", "unable_to_assess", "other", ""},
    "clinical_interpretation": {"no_immediate_concern", "possible_physiological_cupping", "glaucoma_risk_features", "further_assessment", "urgent_ophthalmology", "inconclusive_repeat", "other", ""},
}


def normalise_structured_findings(value):
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValidationError("Structured findings must be an object.")
    result = {}
    for key in ("fundus_quality", "iop_interpretation", "visual_acuity_interpretation", "clinical_interpretation"):
        selected = str(value.get(key) or "").strip()
        if selected not in STRUCTURED_OPTIONS[key]:
            raise ValidationError(f"Invalid structured finding: {key}.")
        result[key] = selected
    for key in ("optic_disc", "retinal_vessels", "retina_macula"):
        selected = value.get(key) or []
        if not isinstance(selected, list) or any(item not in STRUCTURED_OPTIONS[key] for item in selected):
            raise ValidationError(f"Invalid structured finding: {key}.")
        result[key] = list(dict.fromkeys(selected))
    exclusive_normal = {
        "optic_disc": "no_concerning_feature",
        "retinal_vessels": "no_concerning_feature",
        "retina_macula": "no_visible_abnormality",
    }
    for key, normal_value in exclusive_normal.items():
        if normal_value in result[key] and len(result[key]) > 1:
            raise ValidationError(
                f"The reassuring {key.replace('_', ' ')} selection cannot be combined with another finding."
            )
    for eye in ("right", "left"):
        source = value.get(eye) or {}
        if not isinstance(source, dict):
            raise ValidationError(f"Invalid structured findings for the {eye} eye.")
        eye_result = {}
        for key in ("visual_field_reliability", "visual_field_result", "ght"):
            selected = str(source.get(key) or "").strip()
            if selected not in STRUCTURED_OPTIONS[key]:
                raise ValidationError(f"Invalid {eye}-eye structured finding: {key}.")
            eye_result[key] = selected
        for key in ("cup_to_disc_ratio", "vfi", "other_machine_values"):
            eye_result[key] = str(source.get(key) or "").strip()[:120]
        result[eye] = eye_result
    for key in ("optic_disc_other", "retinal_vessels_other", "retina_macula_other", "iop_other", "visual_acuity_other", "clinical_other"):
        result[key] = str(value.get(key) or "").strip()[:1000]
    return result


def validate_screening_safety(report, findings):
    if report.outcome != report.Outcome.NO_IMMEDIATE_CONCERN:
        return
    if findings.get("fundus_quality") in {"significantly_limited", "ungradable"}:
        raise ValidationError(
            "A significantly limited or ungradable photograph cannot support the reassuring outcome."
        )
    for eye in ("right", "left"):
        field = findings.get(eye, {})
        if field.get("visual_field_reliability") in {"unreliable", "unable_to_assess"} or field.get("visual_field_result") in {
            "significant_abnormality", "inconclusive",
        }:
            raise ValidationError(
                "An unreliable, unassessable, significantly abnormal or inconclusive visual field cannot support the reassuring outcome."
            )


def generate_suggested_wording(value):
    findings = normalise_structured_findings(value)
    lines = []
    quality = {
        "good": "The retinal photographs were of sufficient quality for the documented screening assessment.",
        "mildly_limited": "The retinal photographs were mildly limited in quality. No immediate concern was identified within the visible areas, but subtle changes may not be detectable.",
        "significantly_limited": "The retinal photographs were significantly limited in quality, reducing confidence in the assessment. Further examination is advised.",
        "ungradable": "The retinal photographs could not be assessed reliably. Repeat imaging or direct clinical examination is required.",
    }.get(findings.get("fundus_quality"))
    if quality:
        lines.append(quality)
    disc = findings.get("optic_disc", [])
    if "symmetrical_cupping" in disc:
        lines.append("The optic nerves show broadly symmetrical cupping on the available photographs.")
    if "asymmetrical_cupping" in disc:
        lines.append("Optic-disc cupping is asymmetrical between the eyes. Further glaucoma assessment is recommended.")
    if "possible_physiological_cupping" in disc:
        lines.append("The disc appearance may represent physiological cupping; correlation with eye pressure, visual fields and a comprehensive clinical examination is advised.")
    if "rim_thinning_notching" in disc:
        lines.append("Rim thinning or notching was observed on the available optic-disc photograph. Further assessment is recommended.")
    if "disc_haemorrhage" in disc:
        lines.append("An optic-disc haemorrhage was observed. Further clinical assessment is recommended.")
    if "no_concerning_feature" in disc:
        lines.append("No concerning optic-disc feature was identified on the available photographs.")
    if "other" in disc and findings.get("optic_disc_other"):
        lines.append(findings["optic_disc_other"])
    vessel_labels = {"av_nicking": "Arteriovenous nicking was observed.", "vascular_attenuation": "Generalized retinal vascular attenuation was observed.", "no_concerning_feature": "No concerning retinal vascular feature was identified in the photographed areas."}
    lines.extend(vessel_labels[item] for item in findings.get("retinal_vessels", []) if item in vessel_labels)
    if "other" in findings.get("retinal_vessels", []) and findings.get("retinal_vessels_other"):
        lines.append(findings["retinal_vessels_other"])
    retina_labels = {"no_visible_abnormality": "No visible retinal or macular abnormality was identified within the photographed area.", "retinal_haemorrhage": "A retinal haemorrhage was observed.", "retinal_exudate": "Retinal exudate was observed.", "cotton_wool_spot": "A cotton-wool spot was observed.", "macular_concern": "A macular concern was observed and further assessment is recommended."}
    lines.extend(retina_labels[item] for item in findings.get("retina_macula", []) if item in retina_labels)
    if "other" in findings.get("retina_macula", []) and findings.get("retina_macula_other"):
        lines.append(findings["retina_macula_other"])
    for eye, label in (("right", "right"), ("left", "left")):
        vf = findings.get(eye, {})
        reliability, result, ght = vf.get("visual_field_reliability"), vf.get("visual_field_result"), vf.get("ght")
        if reliability in {"unreliable", "reduced_reliability"}:
            lines.append(f"The {label} visual-field result had {reliability.replace('_', ' ')} and should not be used alone to draw a clinical conclusion. Repeat testing is recommended.")
        elif result == "localized_reduction":
            lines.append(f"A localized reduction was recorded in the {label} visual field. Further assessment is recommended to determine its significance.")
        elif result in {"generalized_reduction", "significant_abnormality"}:
            lines.append(f"The {label} visual-field result showed {result.replace('_', ' ')}. Further assessment is recommended.")
        elif result == "inconclusive":
            lines.append(f"The {label} visual-field result was inconclusive. Repeat testing is recommended.")
        elif result in {"within_expected_limits", "essentially_full"}:
            suffix = " Repeat testing is advised to confirm reproducibility." if ght == "borderline" else ""
            lines.append(f"The {label} visual field was {result.replace('_', ' ')} on this screening assessment.{suffix}")
    iop = {
        "within_expected_range": "The recorded eye pressures were within the expected range at the time of screening. A single pressure measurement does not exclude glaucoma.",
        "raised": "The recorded eye pressure was raised in one or both eyes. Further clinical assessment is recommended.",
        "asymmetrical": "A difference in pressure was recorded between the eyes. This should be interpreted alongside optic-disc appearance, visual fields and a comprehensive examination.",
        "unavailable": "Eye-pressure measurements were unavailable for this screening assessment.",
    }.get(findings.get("iop_interpretation"))
    if iop:
        lines.append(iop)
    elif findings.get("iop_interpretation") == "other" and findings.get("iop_other"):
        lines.append(findings["iop_other"])
    va = {
        "within_expected_range": "Visual acuity was within the expected range for the recorded correction.",
        "reduced_right": "Reduced visual acuity was recorded in the right eye.", "reduced_left": "Reduced visual acuity was recorded in the left eye.",
        "reduced_both": "Reduced visual acuity was recorded in both eyes.", "asymmetrical": "Visual acuity was asymmetrical between the eyes.",
        "unable_to_assess": "Visual acuity could not be assessed reliably.",
    }.get(findings.get("visual_acuity_interpretation"))
    if va:
        lines.append(va)
    elif findings.get("visual_acuity_interpretation") == "other" and findings.get("visual_acuity_other"):
        lines.append(findings["visual_acuity_other"])
    interpretation = {
        "no_immediate_concern": "No immediate concern was identified within the areas assessed.",
        "possible_physiological_cupping": "Possible physiological optic-disc cupping was identified; correlation with a comprehensive examination is advised.",
        "glaucoma_risk_features": "Glaucoma-risk features were identified. This screening does not confirm glaucoma, and further assessment is recommended.",
        "further_assessment": "Further assessment is recommended.", "urgent_ophthalmology": "Urgent ophthalmology assessment is recommended.",
        "inconclusive_repeat": "The screening result was inconclusive and repeat testing is required.",
    }.get(findings.get("clinical_interpretation"))
    if interpretation:
        lines.append(interpretation)
    elif findings.get("clinical_interpretation") == "other" and findings.get("clinical_other"):
        lines.append(findings["clinical_other"])
    return "\n\n".join(lines)


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
    structured_findings = normalise_structured_findings(report.structured_findings)
    validate_screening_safety(report, structured_findings)
    tests = {
        "visual_acuity": bool(
            encounter.right_corrected_pinhole_va or encounter.right_unaided_va or encounter.visual_acuity_right
            or encounter.left_corrected_pinhole_va or encounter.left_unaided_va or encounter.visual_acuity_left
        ),
        "iop": bool(encounter.iop_before_dilation_left or encounter.iop_before_dilation_right or encounter.iop_after_dilation_left or encounter.iop_after_dilation_right),
        "visual_fields": bool(report.right_visual_field_result or report.left_visual_field_result or report.selected_visual_field_investigation_ids),
        "fundus": bool(report.right_fundus_result or report.left_fundus_result or report.selected_fundus_upload_ids),
    }
    snapshot = {
        "schema_version": 2,
        "service_package": encounter.service_package,
        "encounter_reference": encounter.encounter_id,
        "assessment_date": encounter.encounter_date.isoformat(),
        "assessment_location": location,
        "tests": tests,
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
        "structured_findings": structured_findings,
        "generated_suggestion": report.generated_suggestion,
        "clinical_summary": report.clinical_summary,
        "limitation": limitation_for_tests(tests),
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


def build_screening_pdf(report, snapshot, audience="patient"):
    if audience not in {"patient", "clinician"}:
        raise ValidationError("Report audience must be patient or clinician.")
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
    result_rows = [["Test", "Right eye", "Left eye"]]
    for key, label in (
        ("visual_acuity", "Visual acuity"),
        ("iop", "Eye pressure (intraocular pressure)"),
        ("visual_fields", "Visual fields"),
        ("fundus", "Fundus assessment"),
    ):
        if snapshot["tests"].get(key):
            value_key = "visual_field" if key == "visual_fields" else key
            result_rows.append([
                label, _display(snapshot["right"].get(value_key)),
                _display(snapshot["left"].get(value_key)),
            ])
    story = [
        header,
        Paragraph(
            "Targeted Retinal and Glaucoma-Risk Screening Report"
            + (" — Clinician Report" if audience == "clinician" else ""),
            title,
        ), Spacer(1, 5*mm),
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
        Paragraph("Tests performed and measured results", styles["Heading2"]),
        Table(result_rows, colWidths=[42*mm, 64*mm, 64*mm], style=TableStyle([
            ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#b8c6d1")),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#12395b")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ])), Spacer(1, 4*mm),
        Paragraph("What the screening showed", styles["Heading2"]),
        Paragraph(_display(snapshot["clinical_summary"]), styles["BodyText"]),
        Paragraph("Outcome", styles["Heading2"]),
        Paragraph(_display(snapshot["outcome_display"]), styles["BodyText"]),
        Paragraph("Recommended next steps", styles["Heading2"]), Paragraph(_display(snapshot["advice"]), styles["BodyText"]),
    ]
    if audience == "clinician":
        findings = snapshot.get("structured_findings") or {}
        detail_rows = [["Clinical detail", "Right eye", "Left eye"]]
        right, left = findings.get("right") or {}, findings.get("left") or {}
        for key, label in (
            ("visual_field_reliability", "Visual-field reliability"),
            ("visual_field_result", "Observed visual-field result"),
            ("ght", "Glaucoma hemifield test"),
            ("vfi", "Visual field index"),
            ("cup_to_disc_ratio", "Cup-to-disc ratio"),
            ("other_machine_values", "Other field measurements"),
        ):
            detail_rows.append([label, _display(right.get(key)), _display(left.get(key))])
        shared = [
            ("Fundus image quality", findings.get("fundus_quality")),
            ("Optic-disc findings", ", ".join(findings.get("optic_disc") or [])),
            ("Retinal vessels", ", ".join(findings.get("retinal_vessels") or [])),
            ("Retina and macula", ", ".join(findings.get("retina_macula") or [])),
            ("IOP interpretation", findings.get("iop_interpretation")),
            ("Visual-acuity interpretation", findings.get("visual_acuity_interpretation")),
        ]
        story.extend([
            Paragraph("Detailed clinical findings", styles["Heading2"]),
            Table(detail_rows, colWidths=[58*mm, 56*mm, 56*mm], style=TableStyle([
                ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#b8c6d1")),
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#12395b")),
                ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                ("VALIGN", (0,0), (-1,-1), "TOP"), ("FONTSIZE", (0,0), (-1,-1), 8),
            ])), Spacer(1, 3*mm),
            Table([[label, _display(value)] for label, value in shared], colWidths=[58*mm, 112*mm], style=TableStyle([
                ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#b8c6d1")),
                ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#eef4f8")),
                ("VALIGN", (0,0), (-1,-1), "TOP"), ("FONTSIZE", (0,0), (-1,-1), 8),
            ])), Spacer(1, 3*mm),
        ])
    story.extend([
        Paragraph("Screening limitation", styles["Heading2"]),
        Paragraph(_display(snapshot["limitation"]), small), Spacer(1, 4*mm),
        Paragraph("Responsible clinician and credentials", styles["Heading2"]),
        Paragraph(_display(
            f"{snapshot['clinician']['display_name']} · {snapshot['clinician']['professional_role']} · "
            f"Registration {snapshot['clinician']['registration_number']}"
            + (f" · {snapshot['clinician']['qualifications']}" if snapshot['clinician'].get('qualifications') else "")
        ), styles["BodyText"]),
    ])
    if footer:
        story.extend([Spacer(1, 4*mm), Paragraph(_display(footer), small)])
    doc.build(story)
    return output.getvalue()


def _fundus_page(report, fundus, ids=None):
    ids = list(report.selected_fundus_upload_ids or []) if ids is None else list(ids)
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


def _draft_overlay(page):
    packet = BytesIO()
    overlay = canvas.Canvas(packet, pagesize=(float(page.mediabox.width), float(page.mediabox.height)))
    overlay.saveState()
    try:
        overlay.setFillAlpha(0.17)
    except Exception:
        pass
    overlay.setFillColor(colors.HexColor("#6b7280"))
    overlay.setFont("Helvetica-Bold", 32)
    overlay.translate(float(page.mediabox.width) / 2, float(page.mediabox.height) / 2)
    overlay.rotate(35)
    overlay.drawCentredString(0, 0, "DRAFT — NOT FOR DISTRIBUTION")
    overlay.restoreState()
    overlay.save()
    packet.seek(0)
    page.merge_page(PdfReader(packet).pages[0])


def _version_assets(report, manifest):
    fundus_ids = [item["id"] for item in manifest if item.get("kind") == "fundus"]
    visual_field_ids = [item["id"] for item in manifest if item.get("kind") == "visual_field_pdf"]
    fundus = {item.pk: item for item in report.encounter.image_uploads.filter(pk__in=fundus_ids)}
    investigations = {
        item.pk: item for item in report.encounter.ocular_investigations.filter(pk__in=visual_field_ids)
    }
    if len(fundus) != len(fundus_ids) or len(investigations) != len(visual_field_ids):
        raise ValidationError("A versioned report attachment is no longer available.")
    for entry in manifest:
        item = fundus.get(entry.get("id")) or investigations.get(entry.get("id"))
        opener = open_image_upload if entry.get("kind") == "fundus" else open_ocular_investigation
        with opener(item, "rb") as source:
            if hashlib.sha256(source.read()).hexdigest() != entry.get("checksum_sha256"):
                raise ValidationError("A versioned report attachment failed its integrity check.")
    return fundus_ids, visual_field_ids, fundus, investigations


def build_complete_pdf(report, snapshot, audience="patient", draft=False, manifest=None):
    if manifest is None:
        manifest, fundus, investigations = _attachment_manifest(report)
        fundus_ids = list(report.selected_fundus_upload_ids or [])
        visual_field_ids = list(report.selected_visual_field_investigation_ids or [])
    else:
        manifest = list(manifest)
        fundus_ids, visual_field_ids, fundus, investigations = _version_assets(report, manifest)
    writer = PdfWriter()
    for page in PdfReader(BytesIO(build_screening_pdf(report, snapshot, audience=audience))).pages:
        if draft:
            _draft_overlay(page)
        writer.add_page(page)
    fundus_pdf = _fundus_page(report, fundus, fundus_ids)
    if fundus_pdf:
        for page in PdfReader(BytesIO(fundus_pdf)).pages:
            if draft:
                _draft_overlay(page)
            writer.add_page(page)
    for item_id in visual_field_ids:
        with open_ocular_investigation(investigations[item_id], "rb") as source:
            attachment = source.read()
        for page in PdfReader(BytesIO(attachment), strict=True).pages:
            if draft:
                _draft_overlay(page)
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
    if not report.outcome or not report.advice.strip() or not report.clinical_summary.strip():
        raise ValidationError("A clinician-confirmed summary, outcome and final advice are required.")
    clinician = professional_snapshot(user, authority)
    clinician.update({"clinic_id": clinic.pk, "clinic_name": clinic.name, "branch_id": branch.pk, "branch_name": branch.name})
    snapshot, checksum, manifest = screening_snapshot(report, clinician)
    if not report.preview_checksum or report.preview_checksum != checksum:
        raise ValidationError("Preview the current screening report before finalization.")
    # Keep the established patient-friendly bytes as the canonical stored copy.
    # The clinician rendering is deterministic from this same immutable snapshot.
    pdf, manifest = build_complete_pdf(report, snapshot, audience="patient", draft=False)
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
