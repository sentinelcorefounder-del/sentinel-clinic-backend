from django.db import models
from django.conf import settings
from patients.models import Patient
from encounters.models import ScreeningEncounter


class StructuredReport(models.Model):
    REPORT_STATUS_CHOICES = [
        ("draft", "Draft"),
        ("under_review", "Under Review"),
        ("signed_off", "Signed Off"),
        ("submitted_to_ops", "Submitted to Ops"),
        ("ops_approved", "Ops Approved"),
        ("ops_rejected", "Ops Rejected"),
        ("issued", "Issued"),
    ]

    URGENCY_OUTCOME_CHOICES = [
        ("routine_followup", "Routine Follow-up"),
        ("early_review", "Early Review"),
        ("urgent_referral", "Urgent Referral"),
        ("ophthalmology_required", "Ophthalmology Required"),
        ("image_retake", "Image Retake"),
    ]

    report_id = models.CharField(max_length=30, unique=True)
    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="reports"
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="reports"
    )
    review_date = models.DateField()
    dr_grade = models.CharField(max_length=50, blank=True)
    maculopathy_grade = models.CharField(max_length=50, blank=True)
    ungradable = models.BooleanField(default=False)
    urgency_outcome = models.CharField(
        max_length=50,
        choices=URGENCY_OUTCOME_CHOICES,
        default="routine_followup"
    )
    recommendation = models.TextField(blank=True)
    next_followup_interval = models.CharField(max_length=50, blank=True)
    report_status = models.CharField(
        max_length=30,
        choices=REPORT_STATUS_CHOICES,
        default="draft"
    )
    notes = models.TextField(blank=True)

    submitted_to_ops_at = models.DateTimeField(null=True, blank=True)
    submitted_to_ops_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports_submitted_to_ops",
    )

    ops_reviewed_at = models.DateTimeField(null=True, blank=True)
    ops_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports_reviewed_by_ops",
    )
    ops_review_note = models.TextField(blank=True, default="")
    payout_email_sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-review_date", "-created_at"]

    def __str__(self):
        return f"{self.report_id} - {self.encounter.encounter_id}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        try:
            self.encounter.update_status_from_related_records()
        except Exception as exc:
            print("StructuredReport encounter status update failed:", exc)

        try:
            from uploads.dataset_pipeline import sync_dataset_from_report
            sync_dataset_from_report(self)
        except Exception as exc:
            print("StructuredReport dataset sync failed:", exc)