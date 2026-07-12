from django.apps import apps
from django.db.models.signals import post_save

from .services import record_patient_event


def _stamp(instance):
    for field in ("updated_at", "created_at", "uploaded_at", "analyzed_at", "paid_at", "referral_date"):
        value = getattr(instance, field, None)
        if value:
            return value
    return None


def patient_saved(sender, instance, created, **kwargs):
    event_type = "patient_registered" if created else "patient_updated"
    record_patient_event(
        patient=instance,
        event_key=f"patient:{instance.pk}:{event_type}:{_stamp(instance)}",
        category="registration",
        event_type=event_type,
        title="Patient registered" if created else "Patient record updated",
        description=(
            f"{instance.first_name} {instance.last_name} was registered in Sentinel."
            if created
            else "Patient demographic or care information was updated."
        ),
        source_type="patient",
        source_id=instance.pk,
        organization=getattr(instance, "assigned_clinic", None),
        occurred_at=_stamp(instance),
    )


def encounter_saved(sender, instance, created, **kwargs):
    patient = getattr(instance, "patient", None)
    if not patient:
        return
    encounter_label = getattr(instance, "encounter_id", str(instance.pk))
    status = getattr(instance, "screening_status", "")
    record_patient_event(
        patient=patient,
        event_key=f"encounter:{instance.pk}:{'created' if created else 'updated'}:{_stamp(instance)}",
        category="encounter",
        event_type="encounter_created" if created else "encounter_updated",
        title="Encounter created" if created else "Encounter updated",
        description=f"Encounter {encounter_label}" + (f" is {status.replace('_', ' ')}." if status else "."),
        source_type="encounter",
        source_id=instance.pk,
        encounter_id=encounter_label,
        occurred_at=_stamp(instance),
    )


def consent_saved(sender, instance, created, **kwargs):
    patient = getattr(instance, "patient", None)
    if not patient:
        encounter = getattr(instance, "encounter", None)
        patient = getattr(encounter, "patient", None)
    if not patient:
        return
    status = getattr(instance, "consent_status", "")
    consent_id = getattr(instance, "consent_id", instance.pk)
    record_patient_event(
        patient=patient,
        event_key=f"consent:{instance.pk}:{status}:{_stamp(instance)}",
        category="consent",
        event_type="consent_completed" if status == "completed" else "consent_updated",
        title="Consent completed" if status == "completed" else "Consent updated",
        description=f"Consent {consent_id} status: {str(status).replace('_', ' ')}.",
        source_type="consent",
        source_id=instance.pk,
        encounter_id=getattr(getattr(instance, "encounter", None), "encounter_id", ""),
        occurred_at=_stamp(instance),
    )


def image_saved(sender, instance, created, **kwargs):
    patient = getattr(instance, "patient", None) or getattr(getattr(instance, "encounter", None), "patient", None)
    if not patient:
        return
    eye = getattr(instance, "eye_laterality", "")
    image_id = getattr(instance, "image_upload_id", instance.pk)
    encounter_id = getattr(getattr(instance, "encounter", None), "encounter_id", "")
    record_patient_event(
        patient=patient,
        event_key=f"image:{instance.pk}:{'uploaded' if created else 'updated'}:{_stamp(instance)}",
        category="imaging",
        event_type="image_uploaded" if created else "image_updated",
        title=f"{str(eye).title()} eye image uploaded" if created else f"{str(eye).title()} eye image updated",
        description=f"Fundus image {image_id} for encounter {encounter_id}.",
        source_type="image_upload",
        source_id=instance.pk,
        encounter_id=encounter_id,
        occurred_at=_stamp(instance),
    )


def ai_saved(sender, instance, created, **kwargs):
    patient = getattr(instance, "patient", None) or getattr(getattr(instance, "encounter", None), "patient", None)
    if not patient:
        return
    prediction = getattr(instance, "prediction", "") or getattr(instance, "severity_label", "") or "Analysis recorded"
    analysis_id = getattr(instance, "analysis_id", instance.pk)
    record_patient_event(
        patient=patient,
        event_key=f"ai:{instance.pk}:{getattr(instance, 'ai_status', '')}:{_stamp(instance)}",
        category="ai",
        event_type="ai_analysis_completed",
        title="AI image analysis completed",
        description=f"Analysis {analysis_id}: {prediction}.",
        source_type="ai_analysis",
        source_id=instance.pk,
        encounter_id=getattr(getattr(instance, "encounter", None), "encounter_id", ""),
        metadata={
            "prediction": getattr(instance, "prediction", None),
            "confidence": getattr(instance, "confidence", None),
            "referable": getattr(instance, "referable", None),
        },
        occurred_at=_stamp(instance),
    )


def report_saved(sender, instance, created, **kwargs):
    patient = getattr(instance, "patient", None)
    if not patient:
        return
    status = getattr(instance, "report_status", "")
    titles = {
        "under_review": "Clinical report created" if created else "Clinical report updated",
        "submitted_to_ops": "Report submitted to Sentinel Ops",
        "returned_to_clinic": "Report returned to clinic",
        "ops_rejected": "Report rejected by Sentinel Ops",
        "ops_approved": "Report approved by Sentinel Ops",
        "issued": "Report issued",
    }
    event_types = {
        "under_review": "report_created" if created else "report_updated",
        "submitted_to_ops": "report_submitted_to_ops",
        "returned_to_clinic": "report_returned",
        "ops_rejected": "report_rejected",
        "ops_approved": "report_approved",
        "issued": "report_issued",
    }
    record_patient_event(
        patient=patient,
        event_key=f"report:{instance.pk}:{status}:{_stamp(instance)}",
        category="report",
        event_type=event_types.get(status, "report_updated"),
        title=titles.get(status, "Clinical report updated"),
        description=(
            getattr(instance, "return_reason", "")
            or getattr(instance, "ops_review_note", "")
            or f"Report {instance.report_id} status: {status.replace('_', ' ')}."
        ),
        source_type="structured_report",
        source_id=instance.pk,
        encounter_id=getattr(getattr(instance, "encounter", None), "encounter_id", ""),
        report_id=getattr(instance, "report_id", ""),
        occurred_at=_stamp(instance),
    )


def referral_saved(sender, instance, created, **kwargs):
    patient = getattr(instance, "patient", None)
    if not patient:
        return
    status = getattr(instance, "referral_status", "")
    record_patient_event(
        patient=patient,
        event_key=f"referral:{instance.pk}:{status}:{_stamp(instance)}",
        category="referral",
        event_type="referral_created" if created else "referral_updated",
        title="Hospital referral created" if created else f"Referral {status.replace('_', ' ')}",
        description=f"Referral {instance.referral_id} status: {status.replace('_', ' ')}.",
        source_type="hospital_referral",
        source_id=instance.pk,
        referral_id=getattr(instance, "referral_id", ""),
        organization=getattr(instance, "source_hospital", None),
        occurred_at=_stamp(instance),
    )


def payment_saved(sender, instance, created, **kwargs):
    referral = getattr(instance, "referral", None)
    patient = getattr(referral, "patient", None)
    if not patient:
        return
    status = getattr(instance, "status", "")
    payment_id = getattr(instance, "payment_id", instance.pk)
    record_patient_event(
        patient=patient,
        event_key=f"payment:{instance.pk}:{status}:{_stamp(instance)}",
        category="payment",
        event_type="payment_created" if created else ("payment_verified" if status == "paid" else "payment_updated"),
        title="Payment received" if status == "paid" else ("Payment created" if created else "Payment updated"),
        description=f"Payment {payment_id} status: {status.replace('_', ' ')}.",
        source_type="ops_payment",
        source_id=instance.pk,
        referral_id=getattr(referral, "referral_id", ""),
        payment_id=payment_id,
        organization=getattr(referral, "source_hospital", None),
        metadata={
            "amount": str(getattr(instance, "amount", "")),
            "currency": getattr(instance, "currency", ""),
        },
        occurred_at=_stamp(instance),
    )


def _connect(app_label, model_name, receiver, uid):
    try:
        model = apps.get_model(app_label, model_name)
        if model:
            post_save.connect(receiver, sender=model, dispatch_uid=uid, weak=False)
    except Exception:
        pass


_connect("patients", "Patient", patient_saved, "audit.patient")
_connect("encounters", "ScreeningEncounter", encounter_saved, "audit.encounter")
_connect("uploads", "ImageUpload", image_saved, "audit.image")
_connect("uploads", "AIAnalysis", ai_saved, "audit.ai")
_connect("reports", "StructuredReport", report_saved, "audit.report")
_connect("referrals", "HospitalReferral", referral_saved, "audit.referral")
_connect("ops", "OpsPayment", payment_saved, "audit.payment")

# Consent model names have varied during development, so connect dynamically.
try:
    consent_app = apps.get_app_config("consents")
    for model in consent_app.get_models():
        field_names = {field.name for field in model._meta.get_fields()}
        if "consent_status" in field_names and ("patient" in field_names or "encounter" in field_names):
            post_save.connect(
                consent_saved,
                sender=model,
                dispatch_uid=f"audit.consent.{model._meta.label_lower}",
                weak=False,
            )
except Exception:
    pass
