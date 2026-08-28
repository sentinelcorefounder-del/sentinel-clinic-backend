import base64
import io
import json
import mimetypes
import re

from django.conf import settings
from openai import OpenAI
from PIL import Image


SYSTEM_PROMPT = """
You are providing clinician-facing ophthalmic decision support for a qualified
eye-care professional. You are not making an autonomous or final diagnosis.
Assess only the supplied encounter data and investigation files. Never invent
missing measurements. If image or test quality is inadequate, say so.

Compare the AI assessment with the optometrist's locked clinical impression and
management plan. Material disagreement includes a different sight-threatening
condition, a missed urgent referral, or materially different urgency.

Return JSON only:
{
  "suspected_conditions": [{"label": "", "certainty": "possible|probable", "eye": "left|right|both|unspecified"}],
  "supporting_findings": [""],
  "differential_diagnoses": [""],
  "suggested_urgency": "routine|priority|urgent|emergency|insufficient_data",
  "suggested_management": "",
  "limitations": [""],
  "agreement_status": "agreement|partial_agreement|material_disagreement|insufficient_data",
  "disagreement_reasons": [""],
  "expert_review_required": true
}
"""


def _sanitise_text(value, patient):
    text = str(value or "")
    identifiers = [
        patient.patient_id, patient.first_name, patient.last_name,
        f"{patient.first_name} {patient.last_name}", patient.phone,
        patient.email, patient.address,
    ]
    if patient.date_of_birth:
        identifiers.extend([
            patient.date_of_birth.isoformat(),
            patient.date_of_birth.strftime("%d/%m/%Y"),
            patient.date_of_birth.strftime("%d-%m-%Y"),
        ])
    for identifier in sorted(
        {str(item).strip() for item in identifiers if str(item or "").strip()},
        key=len,
        reverse=True,
    ):
        text = re.sub(re.escape(identifier), "[REDACTED]", text, flags=re.IGNORECASE)
    return text


def _safe_image_data_url(field_file):
    """Re-encode pixels only, removing EXIF, comments and other source metadata."""
    field_file.open("rb")
    try:
        with Image.open(field_file) as source:
            image = source.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=95, optimize=True)
    finally:
        field_file.close()
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return "image/jpeg", f"data:image/jpeg;base64,{encoded}"


def _data_url(field_file):
    mime_type, _ = mimetypes.guess_type(field_file.name)
    mime_type = mime_type or "application/octet-stream"
    if mime_type.startswith("image/"):
        return _safe_image_data_url(field_file)
    field_file.open("rb")
    try:
        encoded = base64.b64encode(field_file.read()).decode("ascii")
    finally:
        field_file.close()
    return mime_type, f"data:{mime_type};base64,{encoded}"


def run_ocular_ai_review(
    encounter,
    assessment,
    investigations,
    fundus_images,
    deidentified_review_reference,
):
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("Sentinel AI Clinical Review is not configured.")

    patient = encounter.patient
    today = encounter.encounter_date
    age = None
    if patient.date_of_birth and today:
        age = (
            today.year - patient.date_of_birth.year
            - ((today.month, today.day) < (patient.date_of_birth.month, patient.date_of_birth.day))
        )
    clinical_payload = {
        "review_reference": deidentified_review_reference,
        "age_years": age,
        "sex": patient.sex,
        "programme": encounter.programme,
        "visual_acuity": {
            "left_unaided": encounter.left_unaided_va,
            "right_unaided": encounter.right_unaided_va,
            "left_corrected_or_pinhole": encounter.left_corrected_pinhole_va,
            "right_corrected_or_pinhole": encounter.right_corrected_pinhole_va,
        },
        "iop": {
            "left_before_dilation": encounter.iop_before_dilation_left,
            "right_before_dilation": encounter.iop_before_dilation_right,
            "left_after_dilation": encounter.iop_after_dilation_left,
            "right_after_dilation": encounter.iop_after_dilation_right,
        },
        "presenting_complaint": _sanitise_text(assessment.presenting_complaint, patient),
        "ocular_history": _sanitise_text(assessment.ocular_history, patient),
        "anterior_eye_findings": _sanitise_text(assessment.anterior_eye_findings, patient),
        "fundus_findings": _sanitise_text(assessment.fundus_findings, patient),
        "visual_field_summary": _sanitise_text(assessment.visual_field_summary, patient),
        "tonometry_summary": _sanitise_text(assessment.tonometry_summary, patient),
        "optometrist_impression": _sanitise_text(assessment.impression, patient),
        "optometrist_management_plan": _sanitise_text(assessment.management_plan, patient),
        "management_outcome": _sanitise_text(assessment.management_outcome, patient),
        "investigation_manifest": [
            {
                "reference": f"INV-{index + 1}",
                "type": item.investigation_type,
                "laterality": item.laterality,
                "test_type": _sanitise_text(item.test_type, patient),
                "device": _sanitise_text(item.device_name, patient),
                "reliability": item.reliability,
                "reliability_notes": _sanitise_text(item.reliability_notes, patient),
                "interpretation": _sanitise_text(item.interpretation, patient),
            }
            for index, item in enumerate(investigations)
        ],
        "fundus_manifest": [
            {
                "reference": f"FUNDUS-{index + 1}",
                "laterality": item.eye_laterality,
                "quality": item.image_quality,
                "gradable": item.gradable,
            }
            for index, item in enumerate(fundus_images)
        ],
    }

    content = [
        {"type": "input_text", "text": SYSTEM_PROMPT},
        {
            "type": "input_text",
            "text": "CLINICAL ENCOUNTER JSON:\n" + json.dumps(clinical_payload),
        },
    ]
    for index, item in enumerate(investigations):
        from encounters.clinical_assets import open_ocular_investigation
        file_obj = open_ocular_investigation(item)
        try:
            mime_type, data_url = _data_url(file_obj)
        finally:
            file_obj.close()
        if mime_type == "application/pdf":
            content.append({
                "type": "input_file",
                "filename": f"investigation-{index + 1}.pdf",
                "file_data": data_url,
                "detail": "high",
            })
        else:
            content.append({
                "type": "input_image",
                "image_url": data_url,
                "detail": "high",
            })

    for item in fundus_images:
        from uploads.clinical_assets import open_image_upload
        file_obj = open_image_upload(item)
        try:
            _mime_type, data_url = _safe_image_data_url(file_obj)
        finally:
            file_obj.close()
        content.append({
            "type": "input_image",
            "image_url": data_url,
            "detail": "high",
        })

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.responses.create(
        model=settings.OPENAI_VISION_MODEL,
        input=[{"role": "user", "content": content}],
    )
    try:
        return json.loads(response.output_text), response
    except json.JSONDecodeError as exc:
        raise RuntimeError("Sentinel AI returned an invalid structured response.") from exc
