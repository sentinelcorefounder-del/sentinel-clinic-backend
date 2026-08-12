import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

from .models import PartnerNotification


logger = logging.getLogger(__name__)


def notify_organization(
    *, organization, notification_type, title, message, action_path,
    deduplication_key, level="info", entity_type="", entity_id="",
    email_subject="", email_message="",
):
    if organization is None:
        return 0

    recipients = organization.user_links.select_related("user").filter(
        user__is_active=True
    )
    created = 0
    for link in recipients:
        _notification, was_created = PartnerNotification.objects.get_or_create(
            recipient=link.user,
            deduplication_key=deduplication_key,
            defaults={
                "organization": organization,
                "notification_type": notification_type,
                "title": title,
                "message": message,
                "action_path": action_path,
                "level": level,
                "entity_type": entity_type,
                "entity_id": str(entity_id or ""),
            },
        )
        created += int(was_created)

    contact_email = (organization.contact_email or "").strip()
    if created and contact_email and email_subject and email_message:
        frontend_url = getattr(settings, "FRONTEND_URL", "").rstrip("/")
        portal_url = f"{frontend_url}{action_path}" if frontend_url else action_path

        def send_partner_email():
            try:
                send_mail(
                    subject=email_subject,
                    message=f"{email_message}\n\nOpen Sentinel: {portal_url}\n\nSentinel Health",
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    recipient_list=[contact_email],
                    fail_silently=False,
                )
            except Exception:
                logger.exception("Partner notification email failed for organization %s", organization.pk)

        transaction.on_commit(send_partner_email)

    return created
