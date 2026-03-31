from django.db import models
from organizations.models import Organization


class Patient(models.Model):
    SEX_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
        ("other", "Other"),
    ]

    patient_id = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    sex = models.CharField(max_length=10, choices=SEX_CHOICES)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="Nigeria")
    consent_status = models.CharField(max_length=30, default="pending")

    assigned_clinic = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patients",
    )
    referral_id = models.CharField(max_length=50, blank=True)
    referral_status = models.CharField(max_length=50, blank=True)
    appointment_date = models.DateField(null=True, blank=True)
    source_system = models.CharField(max_length=50, default="clinic_portal")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.patient_id})"