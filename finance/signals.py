from django.db.models.signals import post_save
from django.dispatch import receiver

from encounters.models import ScreeningEncounter

from .services import ensure_financial_record


@receiver(post_save, sender=ScreeningEncounter)
def create_encounter_financial_record(sender, instance, created, **kwargs):
    if created:
        ensure_financial_record(instance)
