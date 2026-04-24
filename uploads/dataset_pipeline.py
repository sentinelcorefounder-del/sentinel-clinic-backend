from datetime import date


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


def normalise_report_urgency(report):
    urgency = (report.urgency_outcome or "").strip().lower()

    if urgency in ["urgent_referral", "ophthalmology_required"]:
        return "urgent"

    if urgency in ["early_review"]:
        return "priority"

    if urgency in ["image_retake"]:
        return "not_required"

    return "routine"


def clinician_referable_from_report(report):
    urgency = (report.urgency_outcome or "").strip().lower()
    mac = (report.maculopathy_grade or "").strip().lower()
    dr = (report.dr_grade or "").strip().lower()

    if report.ungradable:
        return True

    if urgency in ["early_review", "urgent_referral", "ophthalmology_required"]:
        return True

    if mac in ["m1", "maculopathy", "maculopathy present", "present"]:
        return True

    if dr in ["moderate_npdr", "moderate npdr", "severe_npdr", "severe npdr", "pdr", "proliferative dr"]:
        return True

    return False


def ai_referable_from_analysis(ai):
    if not ai:
        return None

    if ai.referable is not None:
        return ai.referable

    prediction = (ai.prediction or "").strip().lower()

    if "referable" in prediction and "no referable" not in prediction:
        return True

    if "no referable" in prediction:
        return False

    return None


def calculate_quality_score(report, image_upload, ai, disagreement_flag):
    score = 100

    image_quality = (image_upload.image_quality or "").strip().lower()

    if image_quality == "acceptable":
        score -= 10
    elif image_quality == "poor":
        score -= 30
    elif image_quality == "ungradable":
        score -= 50

    if report.ungradable:
        score -= 35

    if not report.dr_grade:
        score -= 15

    if not report.maculopathy_grade:
        score -= 10

    if not ai:
        score -= 10

    if disagreement_flag and disagreement_flag != "none":
        score -= 20

    score = max(0, min(100, score))
    return score


def quality_flag_from_score(score):
    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def disagreement_from_report_and_ai(report, ai):
    clinician_ref = clinician_referable_from_report(report)
    ai_ref = ai_referable_from_analysis(ai)

    if ai_ref is None:
        return None, "ai_unavailable"

    if clinician_ref != ai_ref:
        return False, "referable_mismatch"

    return True, "none"


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

        ai_agreement, disagreement_flag = disagreement_from_report_and_ai(report, ai)
        quality_score = calculate_quality_score(report, image_upload, ai, disagreement_flag)
        quality_flag = quality_flag_from_score(quality_score)

        DatasetLabel.objects.update_or_create(
            image_upload=image_upload,
            defaults={
                "source_report": report,
                "encounter": report.encounter,
                "patient": patient,
                "consent_confirmed": True,
                "image_quality_label": image_upload.image_quality,
                "dr_grade": report.dr_grade or "unknown",
                "maculopathy_grade": report.maculopathy_grade or "unknown",
                "referable": clinician_referable_from_report(report),
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