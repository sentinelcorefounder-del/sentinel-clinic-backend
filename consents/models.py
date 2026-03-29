from django.db import models
from patients.models import Patient
from encounters.models import ScreeningEncounter


class ConsentRecord(models.Model):
    CONSENT_TYPE_CHOICES = [
        ("care_delivery", "Care Delivery"),
        ("data_sharing", "Data Sharing"),
        ("ai_training", "AI Training"),
        ("research_use", "Research Use"),
    ]

    CONSENT_STATUS_CHOICES = [
        ("granted", "Granted"),
        ("declined", "Declined"),
        ("withdrawn", "Withdrawn"),
        ("expired", "Expired"),
    ]

    consent_id = models.CharField(max_length=30, unique=True)
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="consents"
    )
    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="consents",
        null=True,
        blank=True
    )
    consent_type = models.CharField(max_length=30, choices=CONSENT_TYPE_CHOICES)
    consent_status = models.CharField(max_length=20, choices=CONSENT_STATUS_CHOICES)
    consent_date = models.DateField()
    captured_by = models.CharField(max_length=100, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    withdrawal_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-consent_date", "-created_at"]

    def __str__(self):
        return f"{self.consent_id} - {self.patient.patient_id}"