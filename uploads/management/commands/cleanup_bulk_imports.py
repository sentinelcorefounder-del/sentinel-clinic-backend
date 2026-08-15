from django.core.management.base import BaseCommand
from django.utils import timezone

from uploads.bulk_import import _safe_audit, cleanup_import, cleanup_uncommitted_private_assets
from uploads.models import BulkImageImport


class Command(BaseCommand):
    help = "Expire bulk image imports and retry private staging cleanup."

    def handle(self, *args, **options):
        queryset = BulkImageImport.objects.filter(
            status__in=["processing", "preview", "confirming", "failed", "cancelled", "expired"],
        ).filter(expires_at__lte=timezone.now()) | BulkImageImport.objects.filter(cleanup_pending=True)
        cleaned = 0
        for bulk_import in queryset.distinct():
            if bulk_import.status in {"processing", "preview", "confirming"}:
                bulk_import.status = "expired"
                bulk_import.cleanup_pending = True
                bulk_import.confirmation_token = None
                bulk_import.confirmation_started_at = None
                bulk_import.save(update_fields=["status", "cleanup_pending", "confirmation_token", "confirmation_started_at", "updated_at"])
                _safe_audit(
                    bulk_import.created_by,
                    "bulk_import_expired",
                    bulk_import,
                )
            if cleanup_import(bulk_import):
                cleaned += 1
            if bulk_import.status in {"failed", "cancelled", "expired"}:
                cleanup_uncommitted_private_assets(bulk_import)
        self.stdout.write(self.style.SUCCESS(f"Cleaned {cleaned} bulk import(s)."))
