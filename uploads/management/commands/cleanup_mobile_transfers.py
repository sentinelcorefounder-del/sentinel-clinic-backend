from django.core.management.base import BaseCommand
from django.utils import timezone

from uploads.models import MobileTransferSession
from uploads.views import _delete_pending_object


class Command(BaseCommand):
    help = "Remove expired or cancelled unconfirmed mobile-transfer objects."

    def handle(self, *args, **options):
        cleaned = 0
        sessions = MobileTransferSession.objects.filter(
            status__in=["open", "expired", "cancelled"],
            expires_at__lte=timezone.now(),
        )
        for session in sessions:
            if session.status == "open":
                session.status = "expired"
                session.save(update_fields=["status"])
            for item in session.pending_images.exclude(status="confirmed"):
                try:
                    _delete_pending_object(item)
                except Exception:
                    continue
                item.delete()
                cleaned += 1
        self.stdout.write(self.style.SUCCESS(f"Cleaned {cleaned} pending mobile image(s)."))
