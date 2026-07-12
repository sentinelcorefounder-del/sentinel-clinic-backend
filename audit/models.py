from django.conf import settings
from django.db import models
from django.utils import timezone

from organizations.models import Organization
from patients.models import Patient


class PatientTimelineEvent(models.Model):
    CATEGORY_CHOICES = [
        ("registration", "Registration"),
        ("consent", "Consent"),
        ("encounter", "Encounter"),
        ("imaging", "Imaging"),
        ("ai", "AI"),
        ("report", "Report"),
        ("referral", "Referral"),
        ("payment", "Payment"),
        ("hospital", "Hospital"),
        ("system", "System"),
    ]

    VISIBILITY_CHOICES = [
        ("all", "All authorised portals"),
        ("clinic_ops", "Clinic and Ops"),
        ("hospital_ops", "Hospital and Ops"),
        ("ops_only", "Ops only"),
    ]

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="timeline_events",
    )
    event_key = models.CharField(max_length=255, unique=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    event_type = models.CharField(max_length=80)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    source_type = models.CharField(max_length=80, blank=True, default="")
    source_id = models.CharField(max_length=120, blank=True, default="")
    encounter_id = models.CharField(max_length=120, blank=True, default="")
    report_id = models.CharField(max_length=120, blank=True, default="")
    referral_id = models.CharField(max_length=120, blank=True, default="")
    payment_id = models.CharField(max_length=120, blank=True, default="")

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="patient_timeline_events",
    )
    organization = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="patient_timeline_events",
    )

    visibility = models.CharField(
        max_length=20,
        choices=VISIBILITY_CHOICES,
        default="all",
    )
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["patient", "-occurred_at"]),
            models.Index(fields=["category", "-occurred_at"]),
        ]

    def __str__(self):
        return f"{self.patient.patient_id} - {self.title}"
