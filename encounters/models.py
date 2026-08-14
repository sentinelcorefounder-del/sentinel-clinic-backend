from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.core.exceptions import ValidationError
import uuid

from organizations.models import Organization
from patients.models import Patient


def assessment_session_reference():
    return f"SEN-SESSION-{uuid.uuid4().hex[:12].upper()}"


class AssessmentServiceSession(models.Model):
    class LocationType(models.TextChoices):
        MOBILE = "mobile", "Mobile"
        HOSPITAL = "hospital", "Hospital"
        CLINIC = "clinic", "Clinic"

    class ProviderType(models.TextChoices):
        SENTINEL = "sentinel", "Sentinel"
        SERVICE_PARTNER = "service_partner", "Service partner"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    session_reference = models.CharField(
        max_length=40, unique=True, default=assessment_session_reference, editable=False
    )
    service_date = models.DateField()
    location_type = models.CharField(max_length=20, choices=LocationType.choices)
    participating_organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="assessment_service_sessions"
    )
    service_branch = models.ForeignKey(
        "organizations.OrganizationBranch", on_delete=models.PROTECT,
        null=True, blank=True, related_name="assessment_service_sessions",
    )
    provider_type = models.CharField(max_length=30, choices=ProviderType.choices)
    service_partner = models.ForeignKey(
        Organization, on_delete=models.PROTECT, null=True, blank=True,
        related_name="provided_assessment_service_sessions",
    )
    sentinel_arranged_transport = models.BooleanField(default=False)
    camera_team_rate = models.DecimalField(max_digits=14, decimal_places=2, default=5000)
    logistics_allocation_rate = models.DecimalField(max_digits=14, decimal_places=2, default=2500)
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True, default="")
    configuration_version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_assessment_sessions"
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="activated_assessment_sessions",
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="completed_assessment_sessions",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="cancelled_assessment_sessions",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    IMMUTABLE_TERMS = (
        "service_date", "location_type", "participating_organization_id",
        "service_branch_id", "provider_type", "service_partner_id",
        "sentinel_arranged_transport", "camera_team_rate",
        "logistics_allocation_rate", "currency",
    )

    class Meta:
        ordering = ["-service_date", "-created_at"]
        indexes = [models.Index(fields=["status", "service_date"])]

    def clean(self):
        errors = {}
        participant = self.participating_organization
        if not participant.is_active or participant.organization_type not in {"clinic", "hospital"}:
            errors["participating_organization"] = "Choose an active clinic or hospital."
        if self.service_branch_id and self.service_branch.organization_id != participant.id:
            errors["service_branch"] = "Branch must belong to the participating organisation."
        if self.provider_type == self.ProviderType.SERVICE_PARTNER:
            if not self.service_partner_id or not self.service_partner.is_active or self.service_partner.organization_type != "service_partner":
                errors["service_partner"] = "Choose an active service-partner organisation."
        elif self.service_partner_id:
            errors["service_partner"] = "A Sentinel-provided session cannot have a service partner."
        if self.camera_team_rate < 0:
            errors["camera_team_rate"] = "Amount cannot be negative."
        if self.logistics_allocation_rate < 0:
            errors["logistics_allocation_rate"] = "Amount cannot be negative."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = AssessmentServiceSession.objects.get(pk=self.pk)
            allowed_transitions = {
                self.Status.DRAFT: {self.Status.DRAFT, self.Status.ACTIVE, self.Status.CANCELLED},
                self.Status.ACTIVE: {self.Status.ACTIVE, self.Status.COMPLETED, self.Status.CANCELLED},
                self.Status.COMPLETED: {self.Status.COMPLETED},
                self.Status.CANCELLED: {self.Status.CANCELLED},
            }
            if self.status not in allowed_transitions[previous.status]:
                if previous.status in {self.Status.COMPLETED, self.Status.CANCELLED}:
                    raise ValidationError("Completed or cancelled sessions cannot be reopened.")
                raise ValidationError("This service-session status transition is not permitted.")
            frozen = previous.status != self.Status.DRAFT or previous.encounters.exists()
            if frozen and any(getattr(previous, field) != getattr(self, field) for field in self.IMMUTABLE_TERMS):
                raise ValidationError("Material session terms are frozen after activation or encounter attachment.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.session_reference


class ScreeningEncounter(models.Model):
    PROGRAMME_CHOICES = [
        ("diabetic_screening", "Diabetic Retinal Assessment"),
        ("ocular_diagnostics", "General Ocular Assessment"),
        ("combined_assessment", "Combined Diabetic and Ocular Assessment"),
    ]
    SOURCE_TYPE_CHOICES = [
        ("hospital_referral", "Hospital Referral"),
        ("clinic_direct", "Clinic Direct"),
    ]
    WORKFLOW_ROUTE_CHOICES = [
        ("sentinel_managed", "Sentinel Managed"),
        ("clinic_managed", "Clinic Managed"),
    ]
    PAYMENT_RESPONSIBILITY_CHOICES = [
        ("patient", "Patient"),
        ("clinic", "Clinic"),
        ("hospital", "Hospital"),
        ("programme", "Programme Sponsor"),
        ("waived", "Waived"),
    ]
    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("in_progress", "In Progress"),
        ("images_uploaded", "Images Uploaded"),
        ("under_review", "Under Review"),
        ("report_ready", "Report Ready"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]
    VA_METHOD_CHOICES = [
        ("", "Not Recorded"),
        ("corrected", "Corrected"),
        ("pinhole", "Pinhole"),
    ]

    encounter_id = models.CharField(max_length=30, unique=True)
    patient = models.ForeignKey(
        Patient, on_delete=models.CASCADE, related_name="encounters"
    )
    encounter_date = models.DateField()
    encounter_type = models.CharField(max_length=50, default="retinal_assessment")

    programme = models.CharField(
        max_length=40, choices=PROGRAMME_CHOICES, default="diabetic_screening"
    )
    source_type = models.CharField(
        max_length=30, choices=SOURCE_TYPE_CHOICES, default="hospital_referral"
    )
    workflow_route = models.CharField(
        max_length=30,
        choices=WORKFLOW_ROUTE_CHOICES,
        default="sentinel_managed",
    )
    payment_responsibility = models.CharField(
        max_length=20,
        choices=PAYMENT_RESPONSIBILITY_CHOICES,
        default="hospital",
    )
    originating_organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="originated_screening_encounters",
    )
    service_branch = models.ForeignKey(
        "organizations.OrganizationBranch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="screening_encounters",
    )
    service_session = models.ForeignKey(
        AssessmentServiceSession, on_delete=models.PROTECT, null=True, blank=True,
        related_name="encounters",
    )
    service_delivery_snapshot = models.JSONField(default=dict, blank=True)
    hospital_referral = models.ForeignKey(
        "referrals.HospitalReferral",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="screening_encounters",
    )

    source_override_reason = models.TextField(blank=True, default="")
    source_overridden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="encounter_source_overrides",
    )
    source_overridden_at = models.DateTimeField(null=True, blank=True)

    screening_status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default="scheduled"
    )

    visual_acuity_left = models.CharField(max_length=20, blank=True)
    visual_acuity_right = models.CharField(max_length=20, blank=True)
    left_unaided_va = models.CharField(max_length=20, blank=True)
    right_unaided_va = models.CharField(max_length=20, blank=True)
    left_corrected_pinhole_va = models.CharField(max_length=20, blank=True)
    right_corrected_pinhole_va = models.CharField(max_length=20, blank=True)
    left_va_method = models.CharField(
        max_length=20, choices=VA_METHOD_CHOICES, blank=True, default=""
    )
    right_va_method = models.CharField(
        max_length=20, choices=VA_METHOD_CHOICES, blank=True, default=""
    )

    diabetes_duration = models.CharField(max_length=50, blank=True)
    symptoms_notes = models.TextField(blank=True)
    clinical_notes = models.TextField(blank=True)

    iop_before_dilation_left = models.CharField(max_length=20, blank=True)
    iop_before_dilation_right = models.CharField(max_length=20, blank=True)
    iop_after_dilation_left = models.CharField(max_length=20, blank=True)
    iop_after_dilation_right = models.CharField(max_length=20, blank=True)
    dilation_drops_used = models.CharField(max_length=255, blank=True)
    dilation_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-encounter_date", "-created_at"]

    def __str__(self):
        return f"{self.encounter_id} - {self.patient}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = ScreeningEncounter.objects.filter(pk=self.pk).values(
                "service_session_id", "service_delivery_snapshot"
            ).first()
            if previous and previous["service_delivery_snapshot"]:
                if (
                    previous["service_session_id"] != self.service_session_id
                    or previous["service_delivery_snapshot"] != self.service_delivery_snapshot
                ):
                    raise ValidationError(
                        "The service-session link and delivery snapshot are immutable."
                    )
        return super().save(*args, **kwargs)

    @property
    def includes_diabetic_screening(self):
        return self.programme in {"diabetic_screening", "combined_assessment"}

    @property
    def includes_ocular_diagnostics(self):
        return self.programme in {"ocular_diagnostics", "combined_assessment"}

    def update_status_from_related_records(self):
        if self.screening_status == "cancelled":
            return

        has_uploads = self.image_uploads.exists()
        ocular_completed = False
        if self.includes_ocular_diagnostics:
            try:
                ocular_completed = bool(self.ocular_assessment.completed_at)
            except OcularDiagnosticAssessment.DoesNotExist:
                ocular_completed = False

        try:
            report = self.structured_report
            has_reports = True
            has_completed_report = report.report_status in {
                "issued", "submitted_to_ops", "ops_approved"
            }
        except Exception:
            has_reports = self.reports.exists()
            has_completed_report = self.reports.filter(
                report_status__in=["issued", "submitted_to_ops", "ops_approved"]
            ).exists()

        if self.programme == "ocular_diagnostics" and ocular_completed:
            new_status = "completed"
        elif (
            self.programme == "combined_assessment"
            and ocular_completed
            and has_completed_report
        ):
            new_status = "completed"
        elif (
            self.programme == "diabetic_screening"
            and has_completed_report
        ):
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


class OcularDiagnosticAssessment(models.Model):
    MANAGEMENT_CHOICES = [
        ("routine", "Routine care"),
        ("monitor", "Monitor / review"),
        ("refer_routine", "Routine referral"),
        ("refer_urgent", "Urgent referral"),
        ("refer_emergency", "Emergency referral"),
    ]

    encounter = models.OneToOneField(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="ocular_assessment",
    )
    fundus_photography_performed = models.BooleanField(default=False)
    visual_field_performed = models.BooleanField(default=False)
    tonometry_performed = models.BooleanField(default=False)
    visual_acuity_performed = models.BooleanField(default=True)
    anterior_eye_assessment_performed = models.BooleanField(default=False)

    presenting_complaint = models.TextField(blank=True, default="")
    ocular_history = models.TextField(blank=True, default="")
    anterior_eye_findings = models.TextField(blank=True, default="")
    fundus_findings = models.TextField(blank=True, default="")
    visual_field_summary = models.TextField(blank=True, default="")
    tonometry_summary = models.TextField(blank=True, default="")
    impression = models.TextField(blank=True, default="")
    management_plan = models.TextField(blank=True, default="")
    management_outcome = models.CharField(
        max_length=30,
        choices=MANAGEMENT_CHOICES,
        blank=True,
        default="",
    )
    report_layout = models.CharField(
        max_length=20,
        choices=[
            ("text_only", "Text only"),
            ("with_investigations", "With investigations"),
        ],
        default="text_only",
    )
    selected_fundus_upload_ids = models.JSONField(default=list, blank=True)
    selected_ocular_investigation_ids = models.JSONField(default=list, blank=True)
    attachment_captions = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_ocular_assessments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Ocular assessment - {self.encounter.encounter_id}"


class OcularInvestigation(models.Model):
    INVESTIGATION_TYPES = [
        ("visual_field", "Visual field"),
        ("fundus", "Fundus photograph"),
        ("oct", "OCT"),
        ("anterior_segment", "Anterior segment"),
        ("other", "Other"),
    ]
    LATERALITY_CHOICES = [
        ("left", "Left"),
        ("right", "Right"),
        ("both", "Both"),
        ("not_applicable", "Not applicable"),
    ]
    RELIABILITY_CHOICES = [
        ("reliable", "Reliable"),
        ("borderline", "Borderline"),
        ("unreliable", "Unreliable"),
        ("not_recorded", "Not recorded"),
    ]

    investigation_id = models.CharField(max_length=30, unique=True)
    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="ocular_investigations",
    )
    investigation_type = models.CharField(
        max_length=30, choices=INVESTIGATION_TYPES
    )
    laterality = models.CharField(
        max_length=20, choices=LATERALITY_CHOICES
    )
    test_type = models.CharField(max_length=100, blank=True, default="")
    device_name = models.CharField(max_length=150, blank=True, default="")
    performed_at = models.DateTimeField(null=True, blank=True)
    reliability = models.CharField(
        max_length=20,
        choices=RELIABILITY_CHOICES,
        default="not_recorded",
    )
    reliability_notes = models.TextField(blank=True, default="")
    interpretation = models.TextField(blank=True, default="")
    file = models.FileField(
        upload_to="ocular_investigations/",
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
    )
    original_filename = models.CharField(max_length=255, blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_ocular_investigations",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-performed_at", "-uploaded_at"]

    def __str__(self):
        return f"{self.investigation_id} - {self.encounter.encounter_id}"


class OcularAIReview(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]
    AGREEMENT_CHOICES = [
        ("agreement", "Agreement"),
        ("partial_agreement", "Partial agreement"),
        ("material_disagreement", "Material disagreement"),
        ("insufficient_data", "Insufficient data"),
    ]
    CLINICIAN_DECISION_CHOICES = [
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("modified", "Modified"),
        ("rejected", "Rejected"),
    ]
    PAYMENT_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("charged", "Charged"),
        ("refunded", "Refunded"),
        ("free", "Free clinic review"),
        ("free_failed", "Free review failed"),
    ]

    review_id = models.CharField(max_length=30, unique=True)
    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="ocular_ai_reviews",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requested_ocular_ai_reviews",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending"
    )
    provider = models.CharField(max_length=30, default="hybrid")
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2)
    fee_currency = models.CharField(max_length=3, default="NGN")
    pricing_rule = models.ForeignKey(
        "finance.PricingRule",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ocular_ai_reviews",
    )
    payment_status = models.CharField(
        max_length=20, choices=PAYMENT_STATUS_CHOICES, default="pending"
    )
    charge_ledger_entry = models.OneToOneField(
        "finance.WalletLedgerEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ocular_ai_review_charge",
    )
    refund_ledger_entry = models.OneToOneField(
        "finance.WalletLedgerEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ocular_ai_review_refund",
    )
    clinical_ai_consent = models.ForeignKey(
        "consents.ConsentRecord",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ocular_clinical_ai_reviews",
    )
    training_consent = models.ForeignKey(
        "consents.ConsentRecord",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ocular_training_ai_reviews",
    )
    consent_checked_at = models.DateTimeField(null=True, blank=True)
    privacy_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="privacy_verified_ocular_ai_reviews",
    )
    privacy_verified_at = models.DateTimeField(null=True, blank=True)
    deidentified_review_reference = models.CharField(
        max_length=40, blank=True, default=""
    )
    transmitted_data_manifest = models.JSONField(default=dict, blank=True)
    model_version = models.CharField(max_length=100, blank=True, default="")
    clinician_impression_snapshot = models.TextField()
    clinician_management_snapshot = models.TextField()
    suspected_conditions = models.JSONField(default=list, blank=True)
    supporting_findings = models.JSONField(default=list, blank=True)
    differential_diagnoses = models.JSONField(default=list, blank=True)
    suggested_urgency = models.CharField(max_length=50, blank=True, default="")
    suggested_management = models.TextField(blank=True, default="")
    limitations = models.JSONField(default=list, blank=True)
    agreement_status = models.CharField(
        max_length=30,
        choices=AGREEMENT_CHOICES,
        default="insufficient_data",
    )
    disagreement_reasons = models.JSONField(default=list, blank=True)
    expert_review_required = models.BooleanField(default=False)
    raw_response_json = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    clinician_decision = models.CharField(
        max_length=20,
        choices=CLINICIAN_DECISION_CHOICES,
        default="pending",
    )
    clinician_decision_notes = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="decided_ocular_ai_reviews",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["encounter"],
                name="encounter_one_ocular_ai_review",
            ),
        ]

    def __str__(self):
        return f"{self.review_id} - {self.encounter.encounter_id}"
