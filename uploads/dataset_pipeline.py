from datetime import date
import re


def clean_value(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def latest_ai_training_consent(patient):
    from consents.models import ConsentRecord

    return (
        ConsentRecord.objects.filter(
            patient=patient,
            consent_type="ai_training",
        )
        .order_by("-consent_date", "-created_at")
        .first()
    )


def has_ai_training_consent_granted(patient):
    consent = latest_ai_training_consent(patient)

    if not consent:
        return False

    if consent.consent_status != "granted":
        return False

    if consent.withdrawal_date:
        return False

    if consent.expiry_date and consent.expiry_date < date.today():
        return False

    return True


def va_is_6_12_or_worse(va_value):
    value = clean_value(va_value)

    if not value:
        return False

    very_poor = {"cf", "hm", "pl", "npl"}
    if value in very_poor:
        return True

    match = re.match(r"6(\d+)$", value)
    if not match:
        return False

    denominator = int(match.group(1))
    return denominator >= 12


def get_eye_report_values(report, eye_laterality):
    eye = clean_value(eye_laterality)

    if eye == "left":
        return {
            "unaided_va": report.left_unaided_va or "",
            "corrected_va": report.left_corrected_va or "",
            "dr_grade": report.left_dr_grade or report.dr_grade or "",
            "maculopathy_grade": report.left_maculopathy_grade or report.maculopathy_grade or "",
        }

    if eye == "right":
        return {
            "unaided_va": report.right_unaided_va or "",
            "corrected_va": report.right_corrected_va or "",
            "dr_grade": report.right_dr_grade or report.dr_grade or "",
            "maculopathy_grade": report.right_maculopathy_grade or report.maculopathy_grade or "",
        }

    return {
        "unaided_va": "",
        "corrected_va": "",
        "dr_grade": report.dr_grade or "",
        "maculopathy_grade": report.maculopathy_grade or "",
    }


def normalise_report_urgency(report):
    urgency = clean_value(report.urgency_outcome)

    if urgency in {"urgentreferral", "ophthalmologyrequired"}:
        return "urgent"

    if urgency == "earlyreview":
        return "priority"

    if urgency == "imageretake":
        return "not_required"

    return "routine"


def diabetic_referable_from_eye_values(report, eye_values):
    urgency = clean_value(report.urgency_outcome)
    mac = clean_value(eye_values.get("maculopathy_grade"))
    dr = clean_value(eye_values.get("dr_grade"))

    if report.ungradable:
        return True

    if urgency in {"earlyreview", "urgentreferral", "ophthalmologyrequired"}:
        return True

    if mac in {"m1", "maculopathy", "maculopathypresent", "present", "yes"}:
        return True

    if dr in {
        "r2",
        "r3",
        "r3a",
        "r3s",
        "moderatenpdr",
        "severenpdr",
        "pdr",
        "proliferativedr",
        "proliferativediabeticretinopathy",
    }:
        return True

    return False


def vision_referral_from_eye_values(eye_values):
    corrected_va = eye_values.get("corrected_va") or ""

    if va_is_6_12_or_worse(corrected_va):
        return True, "Corrected/pinhole VA is 6/12 or worse. Full sight test or clinical review recommended."

    return False, ""


def ai_referable_from_analysis(ai):
    if not ai:
        return None

    if ai.referable is not None:
        return ai.referable

    prediction = clean_value(ai.prediction)

    if "noreferable" in prediction:
        return False

    if "referable" in prediction:
        return True

    return None


def disagreement_from_values(diabetic_referable, ai):
    ai_ref = ai_referable_from_analysis(ai)

    if ai_ref is None:
        return None, "ai_unavailable"

    if diabetic_referable is True and ai_ref is False:
        return False, "ai_missed_referable"

    if diabetic_referable is False and ai_ref is True:
        return False, "ai_overcalled_referable"

    return True, "none"


def calculate_quality_score(report, image_upload, ai, disagreement_flag, eye_values):
    score = 100
    image_quality = clean_value(image_upload.image_quality)

    if image_quality == "acceptable":
        score -= 10
    elif image_quality == "poor":
        score -= 30
    elif image_quality == "ungradable":
        score -= 50

    if report.ungradable:
        score -= 35

    if not eye_values.get("dr_grade"):
        score -= 15

    if not eye_values.get("maculopathy_grade"):
        score -= 10

    if not eye_values.get("corrected_va"):
        score -= 10

    if not ai:
        score -= 10

    if disagreement_flag == "ai_missed_referable":
        score -= 35
    elif disagreement_flag == "ai_overcalled_referable":
        score -= 20
    elif disagreement_flag and disagreement_flag != "none":
        score -= 20

    return max(0, min(100, score))


def quality_flag_from_score(score):
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def sync_dataset_from_report(report):
    from uploads.models import ImageUpload, DatasetLabel

    dataset_ready_statuses = {
        "signed_off",
        "submitted_to_ops",
        "ops_approved",
        "issued",
    }

    if report.report_status not in dataset_ready_statuses:
        return

    patient = report.patient

    if not has_ai_training_consent_granted(patient):
        DatasetLabel.objects.filter(source_report=report).delete()
        return

    uploads = ImageUpload.objects.filter(encounter=report.encounter)

    if not uploads.exists():
        return

    for image_upload in uploads:
        ai = getattr(image_upload, "ai_analysis", None)
        eye_values = get_eye_report_values(report, image_upload.eye_laterality)

        diabetic_referable = diabetic_referable_from_eye_values(report, eye_values)
        vision_referral_needed, vision_referral_reason = vision_referral_from_eye_values(eye_values)
        overall_referable = diabetic_referable or vision_referral_needed

        ai_agreement, disagreement_flag = disagreement_from_values(diabetic_referable, ai)

        quality_score = calculate_quality_score(
            report,
            image_upload,
            ai,
            disagreement_flag,
            eye_values,
        )
        quality_flag = quality_flag_from_score(quality_score)

        DatasetLabel.objects.update_or_create(
            image_upload=image_upload,
            defaults={
                "source_report": report,
                "encounter": report.encounter,
                "patient": patient,
                "consent_confirmed": True,
                "image_quality_label": image_upload.image_quality,

                "eye_laterality": image_upload.eye_laterality,
                "unaided_visual_acuity": eye_values["unaided_va"],
                "corrected_visual_acuity": eye_values["corrected_va"],
                "dr_grade": eye_values["dr_grade"] or "unknown",
                "maculopathy_grade": eye_values["maculopathy_grade"] or "unknown",

                "diabetic_referable": diabetic_referable,
                "vision_referral_needed": vision_referral_needed,
                "vision_referral_reason": vision_referral_reason,
                "referable": overall_referable,

                "referral_urgency": normalise_report_urgency(report),
                "clinician_notes": report.recommendation or "",
                "other_findings": report.notes or "",

                "ai_prediction_at_label_time": getattr(ai, "prediction", "") if ai else "",
                "ai_provider_at_label_time": getattr(ai, "provider", "") if ai else "",
                "ai_confidence_at_label_time": getattr(ai, "confidence", None) if ai else None,
                "ai_referable_at_label_time": ai_referable_from_analysis(ai),
                "ai_raw_response_at_label_time": getattr(ai, "raw_response_json", None) if ai else None,

                "report_status_at_label_time": report.report_status,
                "quality_score": quality_score,
                "quality_flag": quality_flag,
                "ai_clinician_agreement": ai_agreement,
                "disagreement_flag": disagreement_flag,
                "label_source": "report_auto",
            },
        )
