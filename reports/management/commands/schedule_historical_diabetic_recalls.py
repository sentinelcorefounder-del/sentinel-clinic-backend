from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from encounters.models import ScreeningEncounter
from patients.models import Patient
from reports.models import HistoricalReportDocument
from reports.recall_services import add_months, set_diabetic_recall

TARGETS = [
    {"patient": 52, "encounter": 50, "historical": 5, "base_date": "2026-08-28", "months": 12},
    {"patient": 53, "encounter": 51, "historical": 7, "base_date": "2026-08-28", "months": 12},
    {"patient": 56, "encounter": 55, "historical": 10, "base_date": "2026-08-29", "months": 12},
    {"patient": 57, "encounter": 56, "historical": 11, "base_date": "2026-08-29", "months": 12},
]

class Command(BaseCommand):
    help = "Dry-run/apply the approved 12-month diabetic recalls for four historical Sentinel Clinic encounters."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply changes. Without this flag the command is read-only.")

    def handle(self, *args, **options):
        apply = options["apply"]
        self.stdout.write("MODE=APPLY" if apply else "MODE=DRY-RUN")
        from datetime import date
        planned=[]
        for target in TARGETS:
            patient = Patient.objects.select_related("assigned_clinic").get(pk=target["patient"])
            encounter = ScreeningEncounter.objects.get(pk=target["encounter"])
            historical = HistoricalReportDocument.objects.get(pk=target["historical"])
            if encounter.patient_id != patient.id or historical.patient_id != patient.id or historical.encounter_id != encounter.id:
                raise CommandError(f"Target mismatch for patient {patient.id}.")
            if not patient.assigned_clinic or patient.assigned_clinic.organization_type != "clinic":
                raise CommandError(f"Patient {patient.id} is not assigned to a clinic.")
            expected=date.fromisoformat(target["base_date"])
            if historical.report_date != expected:
                raise CommandError(f"Historical report date mismatch for patient {patient.id}.")
            due=add_months(expected,target["months"])
            planned.append((patient,encounter,historical,due))
            self.stdout.write(f"patient={patient.id} encounter={encounter.id} historical={historical.id} base={expected} months={target['months']} due={due}")
        if not apply:
            self.stdout.write(self.style.WARNING("Dry run only. No records changed.")); return
        with transaction.atomic():
            for patient, encounter, historical, due in planned:
                if not encounter.is_diabetic:
                    encounter.is_diabetic=True
                    encounter.save(update_fields=["is_diabetic","updated_at"])
                recall=set_diabetic_recall(encounter=encounter, months=12, historical_report=historical, base_date=historical.report_date, actor=None, note="Approved historical diabetic recall backfill.")
                self.stdout.write(self.style.SUCCESS(f"APPLIED patient={patient.id} recall={recall.id} due={due}"))
