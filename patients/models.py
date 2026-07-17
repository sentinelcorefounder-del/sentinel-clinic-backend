from django.conf import settings
from django.db import models
from django.utils import timezone
import uuid
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

    master_patient = models.ForeignKey(
        "MasterPatient",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patient_records",
    )

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


class MasterPatient(models.Model):
    IDENTITY_STATUS_CHOICES = [
        ("active", "Active"),
        ("possible_duplicate", "Possible Duplicate"),
        ("merged", "Merged"),
        ("restricted", "Restricted"),
    ]

    sentinel_patient_id = models.CharField(
        max_length=40,
        unique=True,
        db_index=True,
    )
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    sex = models.CharField(max_length=20, blank=True, default="")
    primary_phone = models.CharField(max_length=30, blank=True, default="")
    primary_email = models.EmailField(blank=True, default="")
    identity_status = models.CharField(
        max_length=30,
        choices=IDENTITY_STATUS_CHOICES,
        default="active",
    )
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["last_name", "first_name", "date_of_birth"]),
            models.Index(fields=["primary_phone"]),
            models.Index(fields=["primary_email"]),
        ]

    def __str__(self):
        return f"{self.sentinel_patient_id} - {self.first_name} {self.last_name}"


class PatientOrganizationIdentity(models.Model):
    IDENTITY_TYPE_CHOICES = [
        ("hospital_mrn", "Hospital MRN"),
        ("clinic_local_id", "Clinic Local ID"),
        ("legacy_patient_id", "Legacy Patient ID"),
        ("other", "Other"),
    ]

    master_patient = models.ForeignKey(
        MasterPatient,
        on_delete=models.CASCADE,
        related_name="organization_identities",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="patient_identities",
    )
    identity_type = models.CharField(
        max_length=30,
        choices=IDENTITY_TYPE_CHOICES,
        default="other",
    )
    local_identifier = models.CharField(max_length=120)
    is_verified = models.BooleanField(default=False)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "identity_type",
                    "local_identifier",
                ],
                name="unique_org_patient_identifier",
            )
        ]
        ordering = ["organization__name", "identity_type"]

    def __str__(self):
        return (
            f"{self.organization.name}: "
            f"{self.identity_type}={self.local_identifier}"
        )


class PatientIdentityReview(models.Model):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("linked", "Linked"),
        ("kept_separate", "Kept Separate"),
        ("dismissed", "Dismissed"),
    ]

    candidate_patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="identity_reviews",
    )
    possible_master_patient = models.ForeignKey(
        MasterPatient,
        on_delete=models.CASCADE,
        related_name="identity_reviews",
    )
    match_score = models.PositiveSmallIntegerField(default=0)
    match_reasons = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="open",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patient_identity_reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["candidate_patient", "possible_master_patient"],
                name="unique_patient_identity_review_pair",
            )
        ]

    def __str__(self):
        return (
            f"{self.candidate_patient.patient_id} -> "
            f"{self.possible_master_patient.sentinel_patient_id}"
        )


class HistoricalRecordAccessRequest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("expired", "Expired"),
        ("revoked", "Revoked"),
    ]

    master_patient = models.ForeignKey(
        MasterPatient,
        on_delete=models.CASCADE,
        related_name="historical_access_requests",
    )
    requesting_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="historical_record_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historical_record_requests",
    )
    purpose = models.TextField()
    consent_reference = models.CharField(max_length=120)
    consent_record = models.ForeignKey(
        "consents.ConsentRecord",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historical_access_requests",
    )
    include_reports = models.BooleanField(default=True)
    include_images = models.BooleanField(default=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historical_record_requests_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=[
                    "requesting_organization",
                    "status",
                    "-created_at",
                ]
            )
        ]

    @property
    def is_currently_active(self):
        if self.status != "approved":
            return False
        if self.revoked_at:
            return False
        return not self.expires_at or self.expires_at > timezone.now()

    def __str__(self):
        return (
            f"{self.master_patient.sentinel_patient_id} - "
            f"{self.requesting_organization.name} - {self.status}"
        )
