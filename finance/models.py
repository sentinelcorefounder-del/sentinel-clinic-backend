from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PartnerContract(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        EXPIRED = "expired", "Expired"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="finance_contracts",
    )
    name = models.CharField(max_length=255)
    programme = models.CharField(max_length=80, default="diabetic_screening")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    currency = models.CharField(max_length=3, default="NGN")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    payment_terms_days = models.PositiveIntegerField(default=0)
    credit_allowed = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-effective_from", "organization__name"]
        indexes = [
            models.Index(fields=["organization", "programme", "status"]),
            models.Index(fields=["effective_from", "effective_to"]),
        ]

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "Effective-to cannot precede effective-from."})

    def __str__(self):
        return f"{self.organization.name} - {self.name}"


class PricingRule(TimeStampedModel):
    contract = models.ForeignKey(
        PartnerContract,
        on_delete=models.PROTECT,
        related_name="pricing_rules",
    )
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    service_type = models.CharField(max_length=80, default="retinal_assessment")
    source_type = models.CharField(max_length=40, blank=True, default="")
    workflow_route = models.CharField(max_length=40, blank=True, default="")
    payment_responsibility = models.CharField(max_length=40, blank=True, default="")
    equipment_owner_type = models.CharField(max_length=40, blank=True, default="")
    min_monthly_volume = models.PositiveIntegerField(null=True, blank=True)
    max_monthly_volume = models.PositiveIntegerField(null=True, blank=True)
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2)
    priority = models.PositiveIntegerField(default=100)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["priority", "-effective_from", "name"]
        indexes = [
            models.Index(fields=["contract", "is_active", "service_type"]),
            models.Index(fields=["source_type", "workflow_route"]),
        ]

    def clean(self):
        if self.gross_amount < 0:
            raise ValidationError({"gross_amount": "Gross amount cannot be negative."})
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "Effective-to cannot precede effective-from."})
        if (
            self.min_monthly_volume is not None
            and self.max_monthly_volume is not None
            and self.max_monthly_volume < self.min_monthly_volume
        ):
            raise ValidationError({"max_monthly_volume": "Maximum volume cannot be below minimum volume."})

    def __str__(self):
        return self.name


class AllocationRule(TimeStampedModel):
    class BeneficiaryRole(models.TextChoices):
        SENTINEL = "sentinel", "Sentinel"
        HOSPITAL = "hospital", "Hospital"
        CLINIC = "clinic", "Clinic"
        FIELD_PARTNER = "field_partner", "Field Partner"
        LOGISTICS = "logistics", "Logistics"
        OTHER = "other", "Other"

    class CalculationType(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        PERCENTAGE = "percentage", "Percentage"

    pricing_rule = models.ForeignKey(
        PricingRule,
        on_delete=models.CASCADE,
        related_name="allocation_rules",
    )
    beneficiary_role = models.CharField(max_length=30, choices=BeneficiaryRole.choices)
    beneficiary_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="finance_allocation_rules",
    )
    label = models.CharField(max_length=120, blank=True, default="")
    calculation_type = models.CharField(max_length=20, choices=CalculationType.choices)
    fixed_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    percentage = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["priority", "id"]

    def clean(self):
        if self.calculation_type == self.CalculationType.FIXED:
            if self.fixed_amount is None:
                raise ValidationError({"fixed_amount": "A fixed allocation requires fixed_amount."})
            if self.fixed_amount < 0:
                raise ValidationError({"fixed_amount": "Fixed amount cannot be negative."})
            if self.percentage is not None:
                raise ValidationError({"percentage": "Percentage must be empty for a fixed allocation."})
        elif self.calculation_type == self.CalculationType.PERCENTAGE:
            if self.percentage is None:
                raise ValidationError({"percentage": "A percentage allocation requires percentage."})
            if self.percentage < 0 or self.percentage > 100:
                raise ValidationError({"percentage": "Percentage must be between 0 and 100."})
            if self.fixed_amount is not None:
                raise ValidationError({"fixed_amount": "Fixed amount must be empty for a percentage allocation."})

    def calculate(self, gross_amount: Decimal) -> Decimal:
        if self.calculation_type == self.CalculationType.FIXED:
            return (self.fixed_amount or Decimal("0.00")).quantize(Decimal("0.01"))
        return (gross_amount * (self.percentage or Decimal("0")) / Decimal("100")).quantize(
            Decimal("0.01")
        )

    def __str__(self):
        return self.label or self.get_beneficiary_role_display()


class EncounterFinancialRecord(TimeStampedModel):
    class Status(models.TextChoices):
        UNPRICED = "unpriced", "Unpriced"
        PRICED = "priced", "Priced"
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
        WALLET_RESERVED = "wallet_reserved", "Wallet reserved"
        APPROVED_CREDIT = "approved_credit", "Approved credit"
        FINANCIALLY_SECURED = "financially_secured", "Financially secured"
        CAPTURED = "captured", "Captured"
        READY_FOR_RELEASE = "ready_for_release", "Ready for release"
        SETTLED = "settled", "Settled"
        REFUNDED = "refunded", "Refunded"
        CANCELLED = "cancelled", "Cancelled"
        EXCEPTION = "exception", "Exception"

    encounter = models.OneToOneField(
        "encounters.ScreeningEncounter",
        on_delete=models.PROTECT,
        related_name="financial_record",
    )
    contract = models.ForeignKey(
        PartnerContract,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="financial_records",
    )
    pricing_rule = models.ForeignKey(
        PricingRule,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="financial_records",
    )
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.UNPRICED)
    currency = models.CharField(max_length=3, default="NGN")
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    allocated_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    outstanding_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    financially_releasable = models.BooleanField(default=False)
    pricing_snapshot = models.JSONField(default=dict, blank=True)
    exception_reason = models.TextField(blank=True, default="")
    priced_at = models.DateTimeField(null=True, blank=True)
    secured_at = models.DateTimeField(null=True, blank=True)
    captured_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "financially_releasable"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Finance - {self.encounter.encounter_id}"


class EncounterAllocation(TimeStampedModel):
    financial_record = models.ForeignKey(
        EncounterFinancialRecord,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    allocation_rule = models.ForeignKey(
        AllocationRule,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="generated_allocations",
    )
    beneficiary_role = models.CharField(max_length=30, choices=AllocationRule.BeneficiaryRole.choices)
    beneficiary_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="encounter_allocations",
    )
    label = models.CharField(max_length=120, blank=True, default="")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")
    rule_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["id"]


class FinancialAuditLog(models.Model):
    financial_record = models.ForeignKey(
        EncounterFinancialRecord,
        on_delete=models.CASCADE,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=80)
    previous_status = models.CharField(max_length=30, blank=True, default="")
    new_status = models.CharField(max_length=30, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finance_audit_entries",
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
