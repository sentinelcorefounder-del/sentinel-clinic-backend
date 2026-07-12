from django.apps import apps
from django.core.management.base import BaseCommand

from audit.signals import (
    ai_saved,
    consent_saved,
    encounter_saved,
    image_saved,
    patient_saved,
    payment_saved,
    referral_saved,
    report_saved,
)


class Command(BaseCommand):
    help = "Backfill append-only patient timeline events from current Sentinel records."

    def handle(self, *args, **options):
        mappings = [
            ("patients", "Patient", patient_saved),
            ("encounters", "ScreeningEncounter", encounter_saved),
            ("uploads", "ImageUpload", image_saved),
            ("uploads", "AIAnalysis", ai_saved),
            ("reports", "StructuredReport", report_saved),
            ("referrals", "HospitalReferral", referral_saved),
            ("ops", "OpsPayment", payment_saved),
        ]

        total = 0
        for app_label, model_name, receiver in mappings:
            try:
                model = apps.get_model(app_label, model_name)
            except Exception:
                continue
            if not model:
                continue
            for instance in model.objects.all().iterator():
                receiver(model, instance, True)
                total += 1

        try:
            consent_app = apps.get_app_config("consents")
            for model in consent_app.get_models():
                fields = {field.name for field in model._meta.get_fields()}
                if "consent_status" in fields and ("patient" in fields or "encounter" in fields):
                    for instance in model.objects.all().iterator():
                        consent_saved(model, instance, True)
                        total += 1
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS(f"Timeline backfill completed. Processed {total} records."))
