import base64
import json
import mimetypes
import os
import uuid
import requests
import io

from PIL import Image
from django.conf import settings
from django.utils import timezone

from .models import AIAnalysis, ImageUpload


SENTINEL_CONFIDENCE_DISPLAY_THRESHOLD = 0.80
OPENAI_IMAGE_MAX_SIZE = 1280
OPENAI_IMAGE_JPEG_QUALITY = 90


def build_absolute_url(base_url, path):
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{base_url.rstrip('/')}{path}"


def create_or_get_analysis(image_upload: ImageUpload, provider: str):
    analysis, _ = AIAnalysis.objects.get_or_create(
        image_upload=image_upload,
        defaults={
            "analysis_id": f"AI-{uuid.uuid4().hex[:10].upper()}",
            "encounter": image_upload.encounter,
            "patient": image_upload.patient,
            "provider": provider,
            "ai_status": "pending",
        },
    )
    return analysis


def call_sentinel_ai(image_upload: ImageUpload):
    analyze_url = (
        f"{settings.SENTINEL_AI_BASE_URL.rstrip('/')}"
        f"{settings.SENTINEL_AI_ANALYZE_PATH}"
    )

    with image_upload.image_file.open("rb") as image_file:
        files = {
            "image": (
                os.path.basename(image_upload.image_file.name),
                image_file,
                "application/octet-stream",
            )
        }
        response = requests.post(analyze_url, files=files, timeout=120)

    response.raise_for_status()
    return response.json()


def normalize_sentinel_result(data):
    prediction = data.get("prediction")

    if prediction == "Invalid image":
        fundus_status = "rejected"
    elif prediction == "Uncertain image":
        fundus_status = "uncertain"
    else:
        fundus_status = data.get("fundus_status") or "accepted"

    referable = data.get("referable")
    if referable is None:
        if prediction == "Referable DR":
            referable = True
        elif prediction == "No Referable DR":
            referable = False

    return {
        "fundus_status": fundus_status,
        "prediction": prediction,
        "referable": referable,
        "confidence": data.get("confidence"),
        "severity": data.get("severity"),
        "severity_label": data.get("severity_label"),
        "message": data.get("message"),
        "disclaimer": data.get("disclaimer"),
        "heatmap_url": build_absolute_url(
            settings.SENTINEL_AI_BASE_URL,
            data.get("heatmap_url")
        ),
        "processed_image_url": build_absolute_url(
            settings.SENTINEL_AI_BASE_URL,
            data.get("processed_image_url")
        ),
        "raw": data,
        "model_version": data.get("model_version") or "sentinel-ai-v1",
    }


def should_use_openai_as_primary(sentinel_normalized):
    confidence = sentinel_normalized.get("confidence")
    severity = sentinel_normalized.get("severity")
    prediction = sentinel_normalized.get("prediction")
    fundus_status = sentinel_normalized.get("fundus_status")
    referable = sentinel_normalized.get("referable")

    if fundus_status in ["rejected", "uncertain", "error"]:
        return True

    if prediction == "Referable DR":
        return True

    if referable is True:
        return True

    try:
        if severity is not None and int(severity) >= 1:
            return True
    except Exception:
        return True

    if confidence is None:
        return True

    try:
        return float(confidence) < SENTINEL_CONFIDENCE_DISPLAY_THRESHOLD
    except Exception:
        return True


def image_file_to_data_url(image_upload: ImageUpload):
    mime_type, _ = mimetypes.guess_type(image_upload.image_file.name)

    if not mime_type:
        mime_type = "image/jpeg"

    with image_upload.image_file.open("rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"


def call_openai_for_observation(image_upload: ImageUpload, sentinel_context=None):
    from openai import OpenAI

    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    image_data_url = image_file_to_data_url(image_upload)

    sentinel_text = ""
    if sentinel_context:
        sentinel_text = f"""
Sentinel AI preliminary output for audit context only:
{json.dumps(sentinel_context, indent=2)}
"""

    prompt = f"""
You are supporting a diabetic eye screening workflow.

You must not provide a final diagnosis.
You must not claim certainty.
You must provide observations for clinician review only.
If there are visible bright focal lesions, haemorrhage-like spots, image quality concerns, or macular-area concerns, describe them cautiously.
Do not say the patient has diabetic retinopathy.
Do not make final referral decisions.

{sentinel_text}

Return JSON only with this exact structure:
{{
  "success": true,
  "fundus_status": "accepted | rejected | uncertain",
  "image_quality": "good | acceptable | poor | ungradable",
  "is_likely_fundus_image": true,
  "visible_observations": [],
  "possible_dr_related_features": [],
  "risk_flag": "low | review_needed | urgent_review_needed",
  "suggested_review_priority": "routine | priority | urgent",
  "draft_note": "",
  "limitations": "This is not a diagnosis. A qualified clinician must review the image."
}}
"""

    response = client.responses.create(
        model=settings.OPENAI_VISION_MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_data_url},
                ],
            }
        ],
    )

    text_output = response.output_text

    try:
        return json.loads(text_output)
    except json.JSONDecodeError:
        return {
            "success": False,
            "fundus_status": "uncertain",
            "image_quality": "ungradable",
            "is_likely_fundus_image": None,
            "visible_observations": [],
            "possible_dr_related_features": [],
            "risk_flag": "review_needed",
            "suggested_review_priority": "priority",
            "draft_note": text_output,
            "limitations": "AI response could not be parsed as JSON. Clinician review is required.",
        }


def analyze_with_sentinel_ai(image_upload: ImageUpload):
    provider = "sentinel"
    analysis = create_or_get_analysis(image_upload, provider)

    analysis.provider = provider
    analysis.ai_status = "running"
    analysis.save(update_fields=["provider", "ai_status"])

    try:
        data = call_sentinel_ai(image_upload)
        normalized = normalize_sentinel_result(data)

        analysis.ai_status = "completed"
        analysis.fundus_status = normalized["fundus_status"]
        analysis.prediction = normalized["prediction"]
        analysis.referable = normalized["referable"]
        analysis.confidence = normalized["confidence"]
        analysis.severity = normalized["severity"]
        analysis.severity_label = normalized["severity_label"]
        analysis.message = normalized["message"]
        analysis.disclaimer = normalized["disclaimer"]
        analysis.heatmap_url = normalized["heatmap_url"]
        analysis.processed_image_url = normalized["processed_image_url"]
        analysis.raw_response_json = normalized["raw"]
        analysis.model_version = normalized["model_version"]
        analysis.analyzed_at = timezone.now()
        analysis.save()

        return analysis

    except Exception as exc:
        analysis.ai_status = "failed"
        analysis.fundus_status = "error"
        analysis.message = str(exc)
        analysis.analyzed_at = timezone.now()
        analysis.save(update_fields=["ai_status", "fundus_status", "message", "analyzed_at"])
        return analysis


def analyze_with_openai(image_upload: ImageUpload):
    provider = "openai"
    analysis = create_or_get_analysis(image_upload, provider)

    analysis.provider = provider
    analysis.ai_status = "running"
    analysis.save(update_fields=["provider", "ai_status"])

    try:
        data = call_openai_for_observation(image_upload)

        analysis.ai_status = "completed"
        analysis.fundus_status = data.get("fundus_status") or "uncertain"
        analysis.prediction = "OpenAI observation for clinician review"
        analysis.referable = None
        analysis.confidence = None
        analysis.severity = None
        analysis.severity_label = None
        analysis.image_quality = data.get("image_quality")
        analysis.risk_flag = data.get("risk_flag")
        analysis.suggested_review_priority = data.get("suggested_review_priority")
        analysis.message = data.get("limitations")
        analysis.draft_note = data.get("draft_note")
        analysis.disclaimer = "OpenAI output is for clinician review only and is not a diagnosis."
        analysis.raw_response_json = data
        analysis.model_version = settings.OPENAI_VISION_MODEL
        analysis.analyzed_at = timezone.now()
        analysis.save()

        return analysis

    except Exception as exc:
        analysis.ai_status = "failed"
        analysis.fundus_status = "error"
        analysis.message = str(exc)
        analysis.analyzed_at = timezone.now()
        analysis.save(update_fields=["ai_status", "fundus_status", "message", "analyzed_at"])
        return analysis


def analyze_with_hybrid_ai(image_upload: ImageUpload):
    provider = "hybrid"
    analysis = create_or_get_analysis(image_upload, provider)

    analysis.provider = provider
    analysis.ai_status = "running"
    analysis.save(update_fields=["provider", "ai_status"])

    try:
        sentinel_data = call_sentinel_ai(image_upload)
        sentinel_normalized = normalize_sentinel_result(sentinel_data)

        use_openai_primary = should_use_openai_as_primary(sentinel_normalized)

        raw_combined = {
            "sentinel": sentinel_normalized["raw"],
            "openai_triggered": use_openai_primary,
            "openai": None,
            "display_policy": {
                "rule": "OpenAI is shown as primary clinician-support output if Sentinel confidence is below 80%, or if Sentinel detects uncertainty, mild-or-above severity, or referable features.",
                "sentinel_confidence": sentinel_normalized.get("confidence"),
                "sentinel_severity": sentinel_normalized.get("severity"),
                "threshold": SENTINEL_CONFIDENCE_DISPLAY_THRESHOLD,
            },
        }

        analysis.provider = provider
        analysis.model_version = (
            f"{sentinel_normalized['model_version']} + conditional-{settings.OPENAI_VISION_MODEL}"
        )
        analysis.heatmap_url = sentinel_normalized["heatmap_url"]
        analysis.processed_image_url = sentinel_normalized["processed_image_url"]

        if use_openai_primary:
            try:
                openai_data = call_openai_for_observation(
                    image_upload,
                    sentinel_context=sentinel_normalized["raw"],
                )
                raw_combined["openai"] = openai_data

                analysis.ai_status = "completed"
                analysis.fundus_status = openai_data.get("fundus_status") or "uncertain"
                analysis.prediction = "OpenAI observation for clinician review"
                analysis.referable = None
                analysis.confidence = sentinel_normalized.get("confidence")
                analysis.severity = sentinel_normalized.get("severity")
                analysis.severity_label = sentinel_normalized.get("severity_label")
                analysis.image_quality = openai_data.get("image_quality")
                analysis.risk_flag = openai_data.get("risk_flag") or "review_needed"
                analysis.suggested_review_priority = (
                    openai_data.get("suggested_review_priority") or "priority"
                )
                analysis.message = (
                    "OpenAI clinician-support observation is shown as the primary AI note because Sentinel AI triggered the hybrid safety rule."
                )
                analysis.draft_note = openai_data.get("draft_note")
                analysis.disclaimer = (
                    "Hybrid AI output is for clinician review only. Sentinel AI output is stored in the audit trail; OpenAI is shown because a safety trigger was met."
                )

            except Exception as openai_exc:
                raw_combined["openai_error"] = str(openai_exc)

                analysis.ai_status = "completed"
                analysis.fundus_status = sentinel_normalized["fundus_status"]
                analysis.prediction = "Clinician review required"
                analysis.referable = None
                analysis.confidence = sentinel_normalized["confidence"]
                analysis.severity = sentinel_normalized["severity"]
                analysis.severity_label = sentinel_normalized["severity_label"]
                analysis.image_quality = None
                analysis.risk_flag = "review_needed"
                analysis.suggested_review_priority = "priority"
                analysis.message = (
                    "A hybrid safety rule was triggered, "
                    f"but OpenAI support failed: {str(openai_exc)}"
                )
                analysis.draft_note = (
                    "Clinician review required. Sentinel AI result is stored in the audit trail "
                    "but not shown as the primary result because a hybrid safety rule was triggered."
                )
                analysis.disclaimer = "OpenAI support failed. Clinician review is required."

        else:
            analysis.ai_status = "completed"
            analysis.fundus_status = sentinel_normalized["fundus_status"]
            analysis.prediction = sentinel_normalized["prediction"]
            analysis.referable = sentinel_normalized["referable"]
            analysis.confidence = sentinel_normalized["confidence"]
            analysis.severity = sentinel_normalized["severity"]
            analysis.severity_label = sentinel_normalized["severity_label"]
            analysis.message = sentinel_normalized["message"]
            analysis.disclaimer = (
                "Sentinel AI output is shown because no hybrid safety trigger was met. Clinician review is still required."
            )
            analysis.risk_flag = "low"
            analysis.suggested_review_priority = "routine"
            analysis.draft_note = (
                "Sentinel AI confidence was above the safety threshold and severity was normal. "
                "No OpenAI support note was generated. Clinician review is still required."
            )

        analysis.raw_response_json = raw_combined
        analysis.analyzed_at = timezone.now()
        analysis.save()

        return analysis

    except Exception as exc:
        analysis.ai_status = "failed"
        analysis.fundus_status = "error"
        analysis.message = str(exc)
        analysis.analyzed_at = timezone.now()
        analysis.save(update_fields=["ai_status", "fundus_status", "message", "analyzed_at"])
        return analysis


def run_ai_analysis(image_upload: ImageUpload):
    provider = getattr(settings, "AI_PROVIDER", "sentinel").lower()

    if provider == "openai":
        return analyze_with_openai(image_upload)

    if provider == "hybrid":
        return analyze_with_hybrid_ai(image_upload)

    return analyze_with_sentinel_ai(image_upload)