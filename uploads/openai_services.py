import base64
import json
import mimetypes

from django.conf import settings
from openai import OpenAI


client = OpenAI(api_key=settings.OPENAI_API_KEY)


def image_file_to_data_url(image_upload):
    file_path = image_upload.image_file.path
    mime_type, _ = mimetypes.guess_type(file_path)

    if not mime_type:
        mime_type = "image/jpeg"

    with open(file_path, "rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"


def analyze_image_with_openai(image_upload):
    image_data_url = image_file_to_data_url(image_upload)

    prompt = """
You are supporting a diabetic eye screening workflow.

You must not provide a final diagnosis.
You must not claim certainty.
You must provide observations for clinician review only.

Return JSON only with this structure:
{
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
}
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
            "limitations": "The AI response could not be parsed as structured JSON. Clinician review is required."
        }