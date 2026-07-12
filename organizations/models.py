from django.db import models


class Organization(models.Model):
    ORGANIZATION_TYPE_CHOICES = [
        ("clinic", "Clinic"),
        ("hospital", "Hospital"),
        ("sentinel", "Sentinel"),
    ]

    clinic_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    contact_email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    organization_type = models.CharField(
        max_length=20,
        choices=ORGANIZATION_TYPE_CHOICES,
        default="clinic",
    )

    logo = models.ImageField(upload_to="clinic_logos/", blank=True, null=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)

    report_signatory_name = models.CharField(max_length=255, blank=True)
    report_signatory_title = models.CharField(max_length=255, blank=True)
    report_signatory_odorbn = models.CharField(max_length=100, blank=True)
    report_footer_note = models.TextField(blank=True)

    screening_fee_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=15000,
    )

    hospital_commission_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    currency = models.CharField(max_length=10, default="NGN")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.clinic_id})"


class OrganizationProfile(models.Model):
    WORKFLOW_MODE_CHOICES = [
        ("sentinel_managed", "Sentinel Managed"),
        ("clinic_managed", "Clinic Managed"),
        ("hybrid", "Hybrid"),
    ]

    REFERRAL_REQUIREMENT_CHOICES = [
        ("required", "Referral Required"),
        ("optional", "Referral Optional"),
        ("not_required", "Referral Not Required"),
    ]

    PATIENT_OWNERSHIP_CHOICES = [
        ("hospital", "Hospital"),
        ("clinic", "Clinic"),
        ("shared", "Shared"),
    ]

    SENTINEL_REVIEW_POLICY_CHOICES = [
        ("mandatory", "Mandatory"),
        ("optional", "Optional"),
        ("unavailable", "Unavailable"),
    ]

    PAYMENT_RESPONSIBILITY_CHOICES = [
        ("patient", "Patient"),
        ("clinic", "Clinic"),
        ("hospital", "Hospital"),
        ("programme", "Programme Sponsor"),
        ("waived", "Waived"),
    ]

    BRANDING_POLICY_CHOICES = [
        ("sentinel_only", "Sentinel Only"),
        ("organization_only", "Organization Only"),
        ("organization_and_sentinel", "Organization + Sentinel"),
        ("hospital_and_sentinel", "Hospital + Sentinel"),
        ("hospital_clinic_sentinel", "Hospital + Clinic + Sentinel"),
    ]

    PROGRAMME_CHOICES = [
        ("diabetic_screening", "Diabetic Screening"),
        ("ocular_diagnostics", "Ocular Diagnostics"),
    ]

    SUBSCRIPTION_TIER_CHOICES = [
        ("pilot", "Pilot"),
        ("clinic_core", "Clinic Core"),
        ("managed_review", "Managed Review"),
        ("hybrid", "Hybrid"),
        ("enterprise", "Enterprise"),
    ]

    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="capability_profile",
    )

    workflow_mode = models.CharField(
        max_length=30,
        choices=WORKFLOW_MODE_CHOICES,
        default="sentinel_managed",
    )
    referral_requirement = models.CharField(
        max_length=30,
        choices=REFERRAL_REQUIREMENT_CHOICES,
        default="required",
    )
    patient_ownership = models.CharField(
        max_length=20,
        choices=PATIENT_OWNERSHIP_CHOICES,
        default="shared",
    )

    can_create_direct_patients = models.BooleanField(default=False)
    can_issue_reports_directly = models.BooleanField(default=False)
    electronic_signature_required = models.BooleanField(default=False)

    sentinel_review_policy = models.CharField(
        max_length=20,
        choices=SENTINEL_REVIEW_POLICY_CHOICES,
        default="mandatory",
    )
    default_payment_responsibility = models.CharField(
        max_length=20,
        choices=PAYMENT_RESPONSIBILITY_CHOICES,
        default="hospital",
    )

    branding_policy = models.CharField(
        max_length=40,
        choices=BRANDING_POLICY_CHOICES,
        default="organization_and_sentinel",
    )
    default_programme = models.CharField(
        max_length=40,
        choices=PROGRAMME_CHOICES,
        default="diabetic_screening",
    )
    subscription_tier = models.CharField(
        max_length=30,
        choices=SUBSCRIPTION_TIER_CHOICES,
        default="pilot",
    )

    ai_enabled = models.BooleanField(default=True)
    clinic_direct_screening_enabled = models.BooleanField(default=False)
    ocular_diagnostics_enabled = models.BooleanField(default=False)

    feature_flags = models.JSONField(default=dict, blank=True)
    settings_notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization__name"]

    def __str__(self):
        return f"{self.organization.name} capability profile"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.workflow_mode == "clinic_managed":
            if not self.can_issue_reports_directly:
                raise ValidationError(
                    {
                        "can_issue_reports_directly": (
                            "Clinic-managed organisations must be allowed to issue reports directly."
                        )
                    }
                )
            if self.sentinel_review_policy == "mandatory":
                raise ValidationError(
                    {
                        "sentinel_review_policy": (
                            "Clinic-managed organisations cannot have mandatory Sentinel review."
                        )
                    }
                )

        if self.workflow_mode == "sentinel_managed":
            if self.can_issue_reports_directly:
                raise ValidationError(
                    {
                        "can_issue_reports_directly": (
                            "Sentinel-managed organisations cannot issue reports directly."
                        )
                    }
                )
            if self.sentinel_review_policy != "mandatory":
                raise ValidationError(
                    {
                        "sentinel_review_policy": (
                            "Sentinel-managed organisations require mandatory Sentinel review."
                        )
                    }
                )

        if self.workflow_mode == "hybrid":
            if not self.can_issue_reports_directly:
                raise ValidationError(
                    {
                        "can_issue_reports_directly": (
                            "Hybrid organisations must be able to issue clinic-managed reports."
                        )
                    }
                )
            if self.sentinel_review_policy != "optional":
                raise ValidationError(
                    {
                        "sentinel_review_policy": (
                            "Hybrid organisations must use optional Sentinel review."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        # Keep the core workflow combinations internally consistent.
        if self.workflow_mode == "sentinel_managed":
            self.can_issue_reports_directly = False
            self.sentinel_review_policy = "mandatory"

        elif self.workflow_mode == "clinic_managed":
            self.can_issue_reports_directly = True
            if self.sentinel_review_policy == "mandatory":
                self.sentinel_review_policy = "unavailable"

        elif self.workflow_mode == "hybrid":
            self.can_issue_reports_directly = True
            self.sentinel_review_policy = "optional"

        self.full_clean()
        super().save(*args, **kwargs)

