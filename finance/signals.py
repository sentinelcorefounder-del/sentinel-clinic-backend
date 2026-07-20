from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver

from encounters.models import ScreeningEncounter

from .models import EncounterFinancialRecord
from .services import ensure_financial_record, sync_encounter_finance_lifecycle


def _record_exception(encounter, message):
    record = ensure_financial_record(encounter)
    if record.status not in {
        EncounterFinancialRecord.Status.CAPTURED,
        EncounterFinancialRecord.Status.SETTLED,
        EncounterFinancialRecord.Status.REFUNDED,
    }:
        record.status = EncounterFinancialRecord.Status.EXCEPTION
        record.exception_reason = str(message)
        record.save(update_fields=["status", "exception_reason", "updated_at"] )


@receiver(post_save, sender=ScreeningEncounter)
def synchronize_encounter_finance(sender, instance, **kwargs):
    # The lifecycle service and wallet operations are idempotent, so repeated
    # saves do not create duplicate charges or ledger entries.
    try:
        sync_encounter_finance_lifecycle(instance)
    except ValidationError as exc:
        messages = getattr(exc, "messages", None) or [str(exc)]
        _record_exception(instance, "; ".join(messages))
    except Exception as exc:
        # Finance problems must remain visible for Ops without destroying the
        # clinical encounter that triggered them.
        _record_exception(instance, f"Automatic finance processing failed: {exc}")
