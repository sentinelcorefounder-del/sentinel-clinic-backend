from django.db import models
from patients.models import Patient


class ScreeningEncounter(models.Model):
    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("in_progress", "In Progress"),
        ("images_uploaded", "Images Uploaded"),
        ("under_review", "Under Review"),
        ("report_ready", "Report Ready"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    encounter_id = models.CharField(max_length=30, unique=True)
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="encounters",
    )
    encounter_date = models.DateField()
    encounter_type = models.CharField(max_length=50, default="diabetic_eye_screening")
    screening_status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="scheduled",
    )

    # Kept for database/backwards compatibility only.
    # VA should now be recorded on StructuredReport by laterality.
    visual_acuity_left = models.CharField(max_length=20, blank=True)
    visual_acuity_right = models.CharField(max_length=20, blank=True)

    diabetes_duration = models.CharField(max_length=50, blank=True)
    symptoms_notes = models.TextField(blank=True)
    clinical_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-encounter_date", "-created_at"]

    def __str__(self):
        return f"{self.encounter_id} - {self.patient}"

    def update_status_from_related_records(self):
        """
        Single source of truth for encounter status movement.

        Desired behaviour:
        - image uploaded -> images_uploaded
        - report created -> under_review
        - report issued/submitted_to_ops/ops_approved -> completed
        """
        if self.screening_status == "cancelled":
            return

        has_uploads = self.image_uploads.exists()
        has_reports = self.reports.exists()
        has_completed_report = self.reports.filter(
            report_status__in=["issued", "submitted_to_ops", "ops_approved"]
        ).exists()

        if has_completed_report:
            new_status = "completed"
        elif has_reports:
            new_status = "under_review"
        elif has_uploads:
            new_status = "images_uploaded"
        else:
            new_status = "scheduled"

        if self.screening_status != new_status:
            self.screening_status = new_status
            self.save(update_fields=["screening_status", "updated_at"])
