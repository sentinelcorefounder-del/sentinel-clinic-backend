from calendar import monthrange
from datetime import date

from django.utils import timezone


def add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def apply_recall_schedule(report):
    if not report.recall_months:
        report.recall_due_date = None
        report.recall_status = "not_set"
        return report

    base_date = (
        report.issued_at.date()
        if report.issued_at
        else report.review_date
    )
    report.recall_due_date = add_months(
        base_date,
        report.recall_months,
    )
    report.recall_status = "scheduled"
    return report


def calculate_live_recall_status(report, today=None):
    today = today or timezone.localdate()

    if report.recall_status in {
        "contacted",
        "booked",
        "completed",
        "deferred",
    }:
        return report.recall_status

    if not report.recall_due_date:
        return "not_set"

    days = (report.recall_due_date - today).days
    if days < 0:
        return "overdue"
    if days == 0:
        return "due"
    if days <= 30:
        return "due_soon"
    return "scheduled"


def calculate_diabetic_recall_live_status(recall, today=None):
    today = today or timezone.localdate()
    if recall.status in {"contacted", "booked", "completed", "deferred"}:
        return recall.status
    days = (recall.due_date - today).days
    if days < 0:
        return "overdue"
    if days == 0:
        return "due"
    if days <= 30:
        return "due_soon"
    return "scheduled"


def set_diabetic_recall(*, encounter, months, actor=None, historical_report=None, base_date=None, note=""):
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from audit.services import record_patient_event
    from .models import DiabeticRecall

    months = int(months)
    if months not in {3, 6, 9, 12, 18, 24}:
        raise ValidationError("Recall interval must be 3, 6, 9, 12, 18 or 24 months.")
    if not encounter.is_diabetic:
        raise ValidationError("This encounter is not marked diabetic.")
    clinic = encounter.patient.assigned_clinic
    if not clinic:
        raise ValidationError("The encounter patient is not assigned to a clinic.")
    if historical_report and (historical_report.encounter_id != encounter.id or historical_report.patient_id != encounter.patient_id):
        raise ValidationError("Historical report does not match this encounter and patient.")
    if base_date is None:
        if historical_report:
            base_date = historical_report.report_date
        else:
            try:
                report = encounter.structured_report
                base_date = report.issued_at.date() if report.issued_at else report.review_date
            except Exception:
                base_date = encounter.encounter_date
    due_date = add_months(base_date, months)
    source_type = "historical_report" if historical_report else "encounter"

    with transaction.atomic():
        recall, created = DiabeticRecall.objects.select_for_update().get_or_create(
            encounter=encounter,
            defaults={
                "patient": encounter.patient, "organization": clinic,
                "historical_report": historical_report, "recall_months": months,
                "base_date": base_date, "due_date": due_date, "status": "scheduled",
                "source_type": source_type, "note": note or "",
                "created_by": actor, "updated_by": actor,
            },
        )
        if not created:
            recall.patient = encounter.patient
            recall.organization = clinic
            recall.historical_report = historical_report or recall.historical_report
            recall.recall_months = months
            recall.base_date = base_date
            recall.due_date = due_date
            recall.status = "scheduled"
            recall.source_type = source_type if historical_report else recall.source_type
            recall.note = note if note is not None else recall.note
            recall.updated_by = actor
            recall.contacted_at = recall.booked_at = recall.completed_at = recall.deferred_at = None
            recall.save()

        record_patient_event(
            patient=encounter.patient,
            event_key=f"diabetic-recall:{recall.pk}:{recall.updated_at.isoformat()}",
            category="recall", event_type="diabetic_recall_scheduled",
            title="Diabetic recall scheduled",
            description=f"Diabetic recall scheduled for {due_date} ({months} months).",
            source_type="encounter", source_id=encounter.pk, encounter_id=encounter.encounter_id,
            actor=actor, organization=clinic, visibility="clinic_ops",
            metadata={"recall_id": recall.pk, "months": months, "base_date": str(base_date), "due_date": str(due_date)},
        )
        return recall
