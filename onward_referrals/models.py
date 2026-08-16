import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def onward_reference():
    year = timezone.now().year
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    suffix = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"SNT-ORF-{year}-{suffix}"


class EncounterResponsibleOptometrist(models.Model):
    encounter = models.OneToOneField(
        "encounters.ScreeningEncounter", on_delete=models.PROTECT,
        related_name="onward_responsibility",
    )
    optometrist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="accepted_onward_responsibilities",
    )
    original_optometrist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="original_onward_responsibilities",
    )
    clinician_name = models.CharField(max_length=255)
    professional_role = models.CharField(max_length=120, default="Optometrist")
    registration_number = models.CharField(max_length=120)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="accepted_onward_responsibility_actions",
    )
    accepted_at = models.DateTimeField()
    takeover_reason = models.TextField(blank=True, default="")
    previous_optometrist = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="transferred_onward_responsibilities",
    )
    updated_at = models.DateTimeField(auto_now=True)


class OnwardReferral(models.Model):
    class Route(models.TextChoices):
        ORIGINATING_HOSPITAL = "originating_hospital", "Originating hospital"
        REGISTERED_HOSPITAL = "registered_hospital", "Registered hospital"
        CLINIC_DOWNLOAD = "clinic_download", "Protected clinic download"

    class Lifecycle(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALIZED = "finalized", "Finalized"
        SUPERSEDED = "superseded", "Superseded"
        VOIDED = "voided", "Voided"

    referral_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    referral_reference = models.CharField(
        max_length=40, unique=True, default=onward_reference, editable=False
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="onward_referrals"
    )
    encounter = models.ForeignKey(
        "encounters.ScreeningEncounter", on_delete=models.PROTECT,
        related_name="onward_referrals",
    )
    originating_clinic = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="originated_onward_referrals",
    )
    branch = models.ForeignKey(
        "organizations.OrganizationBranch", on_delete=models.PROTECT,
        related_name="onward_referrals",
    )
    original_hospital_referral = models.ForeignKey(
        "referrals.HospitalReferral", null=True, blank=True,
        on_delete=models.PROTECT, related_name="onward_referrals",
    )
    retinal_report = models.ForeignKey(
        "reports.StructuredReport", null=True, blank=True,
        on_delete=models.PROTECT, related_name="onward_referrals",
    )
    ocular_assessment = models.ForeignKey(
        "encounters.OcularDiagnosticAssessment", null=True, blank=True,
        on_delete=models.PROTECT, related_name="onward_referrals",
    )
    clinical_sources = models.JSONField(default=list)
    route = models.CharField(max_length=30, choices=Route.choices)
    lifecycle = models.CharField(
        max_length=20, choices=Lifecycle.choices, default=Lifecycle.DRAFT
    )
    current_version = models.ForeignKey(
        "OnwardReferralVersion", null=True, blank=True, on_delete=models.PROTECT,
        related_name="current_for_referrals",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_onward_referrals",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["encounter"],
                condition=models.Q(lifecycle__in=["draft", "finalized"]),
                name="one_active_onward_referral_per_encounter",
            )
        ]


class OnwardReferralVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALIZED = "finalized", "Finalized"
        SUPERSEDED = "superseded", "Superseded"
        VOIDED = "voided", "Voided"

    class Urgency(models.TextChoices):
        EMERGENCY = "emergency", "Emergency"
        URGENT = "urgent", "Urgent"
        EXPEDITED = "expedited", "Expedited"
        ROUTINE = "routine", "Routine"

    referral = models.ForeignKey(
        OnwardReferral, on_delete=models.PROTECT, related_name="versions"
    )
    version_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    urgency = models.CharField(max_length=20, choices=Urgency.choices)
    referral_reason = models.CharField(max_length=255)
    requested_specialist_action = models.TextField()
    relevant_history = models.TextField(blank=True, default="")
    pertinent_findings = models.TextField(blank=True, default="")
    professional_impression = models.TextField(blank=True, default="")
    management_provided = models.TextField(blank=True, default="")
    include_patient_phone = models.BooleanField(default=False)
    recipient_organization = models.ForeignKey(
        "organizations.Organization", null=True, blank=True,
        on_delete=models.PROTECT, related_name="received_onward_referral_versions",
    )
    recipient_department = models.CharField(max_length=160, blank=True, default="")
    responsible_optometrist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="authored_onward_referral_versions",
    )
    author_snapshot = models.JSONField(default=dict)
    patient_snapshot = models.JSONField(default=dict)
    recipient_snapshot = models.JSONField(default=dict)
    clinical_source_snapshot = models.JSONField(default=dict)
    branding_snapshot = models.JSONField(default=dict)
    emergency_escalation_confirmed = models.BooleanField(default=False)
    emergency_escalation_method = models.CharField(max_length=80, blank=True, default="")
    emergency_escalation_note = models.CharField(max_length=255, blank=True, default="")
    emergency_escalation_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="confirmed_onward_emergency_escalations",
    )
    emergency_escalation_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="finalized_onward_referral_versions",
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="superseding_versions",
    )
    amendment_reason = models.TextField(blank=True, default="")
    void_reason = models.TextField(blank=True, default="")
    pdf_object_key = models.CharField(max_length=255, blank=True, default="")
    pdf_checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    pdf_size = models.PositiveIntegerField(null=True, blank=True)
    pdf_generated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_onward_referral_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["referral", "version_number"],
                name="unique_onward_referral_version",
            )
        ]

    IMMUTABLE_AFTER_FINALIZATION = (
        "urgency", "referral_reason", "requested_specialist_action",
        "relevant_history", "pertinent_findings", "professional_impression",
        "management_provided", "include_patient_phone",
        "recipient_organization_id", "recipient_department",
        "responsible_optometrist_id", "author_snapshot", "patient_snapshot",
        "recipient_snapshot", "clinical_source_snapshot", "branding_snapshot",
        "emergency_escalation_confirmed", "emergency_escalation_method",
        "emergency_escalation_note", "emergency_escalation_by_id",
        "emergency_escalation_at", "finalized_by_id", "finalized_at",
        "supersedes_id", "amendment_reason", "pdf_object_key",
        "pdf_checksum_sha256", "pdf_size", "pdf_generated_at",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.status in {
                self.Status.FINALIZED, self.Status.SUPERSEDED,
            }:
                changed = [
                    field for field in self.IMMUTABLE_AFTER_FINALIZATION
                    if getattr(previous, field) != getattr(self, field)
                ]
                if changed:
                    raise ValidationError(
                        "Finalized onward-referral clinical content is immutable."
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Onward-referral versions are append-only and cannot be deleted."
        )


class OnwardReferralAvailability(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"
        SUPERSEDED = "superseded", "Superseded"

    version = models.ForeignKey(
        OnwardReferralVersion, on_delete=models.PROTECT, related_name="availabilities"
    )
    recipient_organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="onward_referral_availabilities",
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.ACTIVE)
    idempotency_key = models.CharField(max_length=100)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="granted_onward_referral_availabilities",
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="revoked_onward_referral_availabilities",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version", "recipient_organization"],
                name="unique_onward_version_availability",
            ),
            models.UniqueConstraint(
                fields=["recipient_organization", "idempotency_key"],
                name="unique_onward_availability_idempotency",
            ),
        ]


class OnwardReferralEvent(models.Model):
    referral = models.ForeignKey(
        OnwardReferral, on_delete=models.PROTECT, related_name="events"
    )
    version = models.ForeignKey(
        OnwardReferralVersion, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    event_type = models.CharField(max_length=80)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="onward_referral_events",
    )
    safe_note = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Onward-referral lifecycle events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Onward-referral lifecycle events are append-only.")


class OnwardReferralAccessEvent(models.Model):
    version = models.ForeignKey(
        OnwardReferralVersion, on_delete=models.PROTECT, related_name="access_events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="onward_referral_access_events",
    )
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="onward_referral_access_events",
    )
    action = models.CharField(
        max_length=20, choices=[("view", "View"), ("download", "Download")]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Onward-referral access events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Onward-referral access events are append-only.")
