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
