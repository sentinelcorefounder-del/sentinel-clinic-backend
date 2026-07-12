from django.utils import timezone

from common.tenant import get_user_organization

from .context import get_current_user
from .models import PatientTimelineEvent


def actor_and_organization(actor=None, organization=None):
    actor = actor or get_current_user()
    if actor and not getattr(actor, "is_authenticated", False):
        actor = None

    if not organization and actor:
        try:
            organization = get_user_organization(actor)
        except Exception:
            organization = None

    return actor, organization


def record_patient_event(
    *,
    patient,
    event_key,
    category,
    event_type,
    title,
    description="",
    source_type="",
    source_id="",
    encounter_id="",
    report_id="",
    referral_id="",
    payment_id="",
    actor=None,
    organization=None,
    visibility="all",
    metadata=None,
    occurred_at=None,
):
    if not patient or not event_key:
        return None

    actor, organization = actor_and_organization(actor, organization)

    event, _ = PatientTimelineEvent.objects.get_or_create(
        event_key=event_key,
        defaults={
            "patient": patient,
            "category": category,
            "event_type": event_type,
            "title": title,
            "description": description or "",
            "source_type": source_type or "",
            "source_id": str(source_id or ""),
            "encounter_id": str(encounter_id or ""),
            "report_id": str(report_id or ""),
            "referral_id": str(referral_id or ""),
            "payment_id": str(payment_id or ""),
            "actor": actor,
            "organization": organization,
            "visibility": visibility,
            "metadata": metadata or {},
            "occurred_at": occurred_at or timezone.now(),
        },
    )
    return event
