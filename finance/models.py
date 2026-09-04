from decimal import Decimal
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q




SENTINEL_TREASURY_CLINICAL_ORG_CODES = {"SNT-CLINIC"}


def is_sentinel_treasury_organization(organization):
    return bool(
        organization
        and organization.is_active
        and (
            organization.organization_type == "sentinel"
            or organization.clinic_id in SENTINEL_TREASURY_CLINICAL_ORG_CODES
        )
    )


class TreasuryExpenseCategory(models.TextChoices):
    SALARY_PAYROLL = "salary_payroll", "Salary / payroll"
    CONTRACTOR = "contractor", "Contractor"
    HOSTING_SOFTWARE = "hosting_software", "Hosting / software"
    FIELD_OPERATIONS = "field_operations", "Field operations"
    MARKETING_ADMIN = "marketing_administration", "Marketing / administration"
    EQUIPMENT_SUPPLIES = "equipment_supplies", "Equipment / supplies"
    TAX_PROFESSIONAL = "tax_professional_fees", "Tax / professional fees"
    FOUNDER_REIMBURSEMENT = "founder_reimbursement", "Founder reimbursement"
    INTERNAL_TRANSFER = "internal_account_transfer", "Internal account transfer"
    OTHER = "other_operating_expense", "Other approved operating expense"


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
        if self.status == self.Status.ACTIVE and self.organization_id:
            overlapping = PartnerContract.objects.filter(
                organization_id=self.organization_id,
                programme=self.programme,
                status=self.Status.ACTIVE,
            ).exclude(pk=self.pk)
            if self.effective_to:
                overlapping = overlapping.filter(effective_from__lte=self.effective_to)
            overlapping = overlapping.filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from)
            )
            if overlapping.exists():
                raise ValidationError(
                    "Only one active contract may cover an organisation, programme, and date."
                )

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
    version = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="superseded_by",
    )

    class Meta:
        ordering = ["priority", "-effective_from", "name"]
        indexes = [
            models.Index(fields=["contract", "is_active", "service_type"]),
            models.Index(fields=["source_type", "workflow_route"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "name", "version"],
                name="fin_unique_pricing_rule_version",
            )
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
        if self.supersedes_id and self.supersedes_id == self.id:
            raise ValidationError({"supersedes": "A pricing rule cannot supersede itself."})
        if self.supersedes_id and self.supersedes.contract_id != self.contract_id:
            raise ValidationError({"supersedes": "A pricing rule can only supersede a rule in the same contract."})
        if self.contract_id:
            organization_type = self.contract.organization.organization_type
            clinic_only_services = {"ocular_ai_review", "sentinel_ai_analysis"}
            if organization_type == "hospital" and self.service_type in clinic_only_services:
                raise ValidationError(
                    {"service_type": "Clinic AI charges cannot be added to a hospital contract."}
                )
            if self.service_type == "ocular_ai_review" and organization_type != "clinic":
                raise ValidationError(
                    {"service_type": "Ocular AI review pricing is available only for clinic contracts."}
                )

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

    class BeneficiarySource(models.TextChoices):
        FIXED = "fixed", "Fixed organisation"
        REFERRING_HOSPITAL = "referring_hospital", "Encounter referring hospital"
        TESTING_CLINIC = "testing_clinic", "Encounter testing clinic"

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
    beneficiary_source = models.CharField(
        max_length=30,
        choices=BeneficiarySource.choices,
        default=BeneficiarySource.FIXED,
        help_text="How the beneficiary is resolved when an encounter is priced.",
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
        if self.beneficiary_source != self.BeneficiarySource.FIXED and self.beneficiary_organization_id:
            raise ValidationError(
                {"beneficiary_organization": "A dynamic beneficiary cannot also have a fixed organisation."}
            )
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
    class ServicePathway(models.TextChoices):
        HOSPITAL_REFERRED = "hospital_referred", "Hospital referred"
        CLINIC_DIRECT = "clinic_direct", "Clinic direct"

    class PayerType(models.TextChoices):
        PATIENT = "patient", "Patient"
        ORGANIZATION = "organization", "Hospital or clinic"
        PROGRAMME = "programme", "Programme sponsor"
        WAIVED = "waived", "Waived"

    class CollectorType(models.TextChoices):
        SENTINEL = "sentinel", "Sentinel"
        HOSPITAL = "hospital", "Hospital"
        CLINIC = "clinic", "Clinic"
        PROGRAMME = "programme", "Programme sponsor"
        NONE = "none", "No collector"

    class PaymentMethod(models.TextChoices):
        UNSET = "unset", "Not selected"
        PAYSTACK = "paystack", "Paystack"
        WALLET = "wallet", "Prefunded wallet"
        BANK_TRANSFER = "bank_transfer", "Approved bank transfer"
        POS = "pos", "Sentinel POS"
        AUTHORIZED_CREDIT = "authorized_credit", "Authorised credit"
        WAIVED = "waived", "Waived"

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
    payer_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payer_financial_records",
        help_text="Organisation financially responsible for this encounter.",
    )
    collecting_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="collected_financial_records",
        help_text="Partner organisation that collected the patient's money, if any.",
    )
    service_pathway = models.CharField(
        max_length=30,
        choices=ServicePathway.choices,
        default=ServicePathway.HOSPITAL_REFERRED,
    )
    payer_type = models.CharField(
        max_length=20,
        choices=PayerType.choices,
        default=PayerType.ORGANIZATION,
    )
    collector_type = models.CharField(
        max_length=20,
        choices=CollectorType.choices,
        default=CollectorType.NONE,
    )
    payment_method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        default=PaymentMethod.UNSET,
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
    class Status(models.TextChoices):
        PENDING_SERVICE = "pending_service", "Pending service"
        EARNED = "earned", "Earned"
        SETTLEMENT_PENDING = "settlement_pending", "Settlement pending"
        SETTLED = "settled", "Settled"
        REVERSED = "reversed", "Reversed"

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
    beneficiary_source = models.CharField(
        max_length=30,
        choices=AllocationRule.BeneficiarySource.choices,
        default=AllocationRule.BeneficiarySource.FIXED,
    )
    label = models.CharField(max_length=120, blank=True, default="")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")
    rule_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING_SERVICE)
    earned_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(
                fields=["beneficiary_organization", "status"],
                name="fin_alloc_benef_status_idx",
            ),
            models.Index(fields=["status", "created_at"], name="fin_alloc_status_created_idx"),
        ]


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

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Financial audit records are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Financial audit records are immutable.")


class OrganizationWallet(TimeStampedModel):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="finance_wallets",
    )
    currency = models.CharField(max_length=3, default="NGN")
    is_active = models.BooleanField(default=True)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["organization__name", "currency"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "currency"],
                name="finance_unique_org_wallet_currency",
            )
        ]

    def clean(self):
        if self.credit_limit < 0:
            raise ValidationError({"credit_limit": "Credit limit cannot be negative."})

    @property
    def available_balance(self):
        return self.ledger_entries.aggregate(total=models.Sum("available_delta"))["total"] or Decimal("0.00")

    @property
    def reserved_balance(self):
        return self.ledger_entries.aggregate(total=models.Sum("reserved_delta"))["total"] or Decimal("0.00")

    @property
    def spendable_balance(self):
        return self.available_balance + self.credit_limit

    def __str__(self):
        return f"{self.organization.name} wallet ({self.currency})"


class ServiceAllowance(TimeStampedModel):
    """A controlled authority to deliver services before cash funding arrives."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        EXHAUSTED = "exhausted", "Exhausted"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="service_allowances",
    )
    contract = models.ForeignKey(
        PartnerContract, on_delete=models.PROTECT, null=True, blank=True,
        related_name="service_allowances",
    )
    name = models.CharField(max_length=255)
    currency = models.CharField(max_length=3, default="NGN")
    monetary_limit = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    patient_limit = models.PositiveIntegerField(null=True, blank=True)
    valid_from = models.DateField()
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="approved_service_allowances",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "status"], name="fin_allow_org_status_idx"),
            models.Index(fields=["status", "expires_at"], name="fin_allow_status_exp_idx"),
        ]

    def clean(self):
        if self.monetary_limit is None and self.patient_limit is None:
            raise ValidationError("An allowance requires a monetary limit, a patient limit, or both.")
        if self.monetary_limit is not None and self.monetary_limit <= 0:
            raise ValidationError({"monetary_limit": "Monetary limit must be greater than zero."})
        if self.patient_limit is not None and self.patient_limit <= 0:
            raise ValidationError({"patient_limit": "Patient limit must be greater than zero."})
        if self.contract_id and self.contract.organization_id != self.organization_id:
            raise ValidationError({"contract": "Contract and allowance organisation must match."})

    @property
    def reserved_amount(self):
        return self.reservations.filter(status=ServiceAllowanceReservation.Status.ACTIVE).aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0.00")

    @property
    def reserved_patients(self):
        return self.reservations.filter(status=ServiceAllowanceReservation.Status.ACTIVE).count()

    def __str__(self):
        return f"{self.organization.name} - {self.name}"


class ServiceAllowanceReservation(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        FUNDED = "funded", "Replaced by genuine funding"
        RELEASED = "released", "Released"

    allowance = models.ForeignKey(ServiceAllowance, on_delete=models.PROTECT, related_name="reservations")
    financial_record = models.OneToOneField(
        EncounterFinancialRecord, on_delete=models.PROTECT, related_name="allowance_reservation"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    reserved_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="service_allowance_reservations",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["allowance", "status"], name="fin_allow_res_status_idx")]

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({"amount": "Reserved amount must be greater than zero."})
        if self.allowance_id and self.currency != self.allowance.currency:
            raise ValidationError({"currency": "Reservation currency must match the allowance."})

    def __str__(self):
        return f"Allowance reservation {self.pk}"


class WalletReservation(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PARTIALLY_CAPTURED = "partially_captured", "Partially captured"
        CAPTURED = "captured", "Captured"
        PARTIALLY_RELEASED = "partially_released", "Partially released"
        RELEASED = "released", "Released"
        CANCELLED = "cancelled", "Cancelled"

    wallet = models.ForeignKey(
        OrganizationWallet,
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    financial_record = models.ForeignKey(
        EncounterFinancialRecord,
        on_delete=models.PROTECT,
        related_name="wallet_reservations",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    captured_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    released_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.ACTIVE)
    idempotency_key = models.CharField(max_length=120, unique=True)
    reference = models.CharField(max_length=120, blank=True, default="")
    reserved_at = models.DateTimeField(auto_now_add=True)
    captured_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wallet", "status"], name="finance_wal_wallet__1c9971_idx"),
            models.Index(fields=["financial_record", "status"], name="finance_wal_financi_f5d88f_idx"),
        ]

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({"amount": "Reservation amount must be greater than zero."})
        if self.captured_amount < 0 or self.released_amount < 0:
            raise ValidationError("Captured and released amounts cannot be negative.")
        if self.captured_amount + self.released_amount > self.amount:
            raise ValidationError("Captured and released amounts cannot exceed the reservation amount.")
        if self.wallet_id and self.currency != self.wallet.currency:
            raise ValidationError({"currency": "Reservation currency must match the wallet currency."})
        if self.financial_record_id and self.currency != self.financial_record.currency:
            raise ValidationError({"currency": "Reservation currency must match the financial record currency."})

    @property
    def remaining_amount(self):
        return self.amount - self.captured_amount - self.released_amount

    def __str__(self):
        return f"Reservation {self.id} - {self.amount} {self.currency}"


class WalletLedgerEntry(models.Model):
    class EntryType(models.TextChoices):
        TOP_UP = "top_up", "Top up"
        SERVICE_RESERVATION = "service_reservation", "Service reservation"
        SERVICE_CAPTURE = "service_capture", "Service capture"
        RESERVATION_RELEASE = "reservation_release", "Reservation release"
        REFUND = "refund", "Refund"
        REVERSAL = "reversal", "Reversal"
        ADJUSTMENT = "adjustment", "Adjustment"
        SETTLEMENT = "settlement", "Settlement"
        TRANSFER = "transfer", "Transfer"
        WRITE_OFF = "write_off", "Write off"

    wallet = models.ForeignKey(
        OrganizationWallet,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    entry_type = models.CharField(max_length=40, choices=EntryType.choices)
    available_delta = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reserved_delta = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="NGN")
    financial_record = models.ForeignKey(
        EncounterFinancialRecord,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="wallet_ledger_entries",
    )
    reservation = models.ForeignKey(
        WalletReservation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    related_entry = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="follow_up_entries",
    )
    idempotency_key = models.CharField(max_length=120, unique=True)
    reference = models.CharField(max_length=120, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_ledger_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["wallet", "created_at"], name="finance_wal_wallet__c065f5_idx"),
            models.Index(fields=["entry_type", "created_at"], name="finance_wal_entry_t_04ed32_idx"),
            models.Index(fields=["financial_record", "created_at"], name="finance_wal_financi_49d1f9_idx"),
        ]

    def clean(self):
        if self.available_delta == 0 and self.reserved_delta == 0:
            raise ValidationError("A ledger entry must change the available or reserved balance.")
        if self.wallet_id and self.currency != self.wallet.currency:
            raise ValidationError({"currency": "Ledger currency must match the wallet currency."})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Wallet ledger entries are immutable and cannot be edited.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Wallet ledger entries are immutable and cannot be deleted.")

    def __str__(self):
        return f"{self.get_entry_type_display()} - {self.wallet}"


def bank_transfer_proof_path(instance, filename):
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"finance/bank-transfer-proofs/{instance.request_reference}/{uuid.uuid4().hex}.{suffix}"


def bank_transfer_request_reference():
    return f"SEN-BT-{uuid.uuid4().hex[:12].upper()}"


class BillingProfile(TimeStampedModel):
    """Ops-managed legal identity and bank instructions used on finance documents."""

    legal_entity_name = models.CharField(max_length=255, default="Afriophthalmics")
    trading_name = models.CharField(max_length=255, default="Sentinel")
    registered_address = models.TextField(blank=True, default="")
    company_registration_number = models.CharField(max_length=100, blank=True, default="")
    tax_identification_number = models.CharField(max_length=100, blank=True, default="")
    finance_email = models.EmailField(blank=True, default="")
    finance_phone = models.CharField(max_length=60, blank=True, default="")
    bank_name = models.CharField(max_length=180, blank=True, default="")
    bank_account_name = models.CharField(max_length=180, blank=True, default="")
    bank_account_number = models.CharField(max_length=80, blank=True, default="")
    bank_branch_code = models.CharField(max_length=80, blank=True, default="")
    currency = models.CharField(max_length=3, default="NGN")
    transfer_instructions = models.TextField(blank=True, default="")
    funding_request_prefix = models.CharField(max_length=20, default="SEN-BT")
    receipt_prefix = models.CharField(max_length=20, default="SEN-RCPT")
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="updated_billing_profiles",
    )

    class Meta:
        ordering = ["-is_active", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"], condition=Q(is_active=True),
                name="fin_single_active_billing_profile",
            )
        ]

    @property
    def is_complete(self):
        return all((self.legal_entity_name, self.bank_name, self.bank_account_name,
                    self.bank_account_number, self.currency))

    def clean(self):
        import re
        for field_name in ("funding_request_prefix", "receipt_prefix"):
            if not re.fullmatch(r"[A-Z0-9-]{2,20}", getattr(self, field_name, "")):
                raise ValidationError({field_name: "Use 2–20 uppercase letters, numbers or hyphens."})
        if self.is_active:
            duplicate = BillingProfile.objects.filter(is_active=True).exclude(pk=self.pk)
            if duplicate.exists():
                raise ValidationError("Only one billing profile may be active.")

    def __str__(self):
        return f"{self.legal_entity_name} ({self.currency})"


class BankTransferFundingRequest(TimeStampedModel):
    class Status(models.TextChoices):
        AWAITING_TRANSFER = "awaiting_transfer", "Awaiting transfer"
        PROOF_SUBMITTED = "proof_submitted", "Proof submitted"
        UNDER_VERIFICATION = "under_verification", "Under verification"
        VERIFIED = "verified", "Verified"
        CREDITED = "credited", "Credited"
        UNDERPAID = "underpaid", "Underpaid"
        OVERPAID = "overpaid", "Overpaid"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
        REVERSED = "reversed", "Reversed"

    wallet = models.ForeignKey(
        OrganizationWallet,
        on_delete=models.PROTECT,
        related_name="bank_transfer_funding_requests",
    )
    request_reference = models.CharField(
        max_length=32,
        unique=True,
        default=bank_transfer_request_reference,
        editable=False,
    )
    requested_amount = models.DecimalField(max_digits=14, decimal_places=2)
    received_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.AWAITING_TRANSFER)
    expires_at = models.DateTimeField(null=True, blank=True)
    proof = models.FileField(upload_to=bank_transfer_proof_path, null=True, blank=True)
    proof_submitted_at = models.DateTimeField(null=True, blank=True)
    bank_transaction_reference = models.CharField(max_length=120, blank=True, default="")
    value_date = models.DateField(null=True, blank=True)
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_bank_transfer_funding",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_bank_transfer_funding",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_bank_transfer_funding",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    ledger_entry = models.OneToOneField(
        WalletLedgerEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bank_transfer_funding_request",
    )
    notes = models.TextField(blank=True, default="")
    rejection_reason = models.TextField(blank=True, default="")
    billing_snapshot = models.JSONField(default=dict, blank=True)
    customer_snapshot = models.JSONField(default=dict, blank=True)
    receipt_reference = models.CharField(max_length=40, unique=True, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wallet", "status"], name="fin_bank_wallet_status_idx"),
            models.Index(fields=["status", "created_at"], name="fin_bank_status_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["bank_transaction_reference"],
                condition=~models.Q(bank_transaction_reference=""),
                name="fin_unique_bank_transaction_ref",
            )
        ]

    def clean(self):
        if self.requested_amount <= 0:
            raise ValidationError({"requested_amount": "Requested amount must be greater than zero."})
        if self.received_amount is not None and self.received_amount <= 0:
            raise ValidationError({"received_amount": "Received amount must be greater than zero."})
        if self.wallet_id and self.currency != self.wallet.currency:
            raise ValidationError({"currency": "Funding currency must match the wallet currency."})

    def __str__(self):
        return f"{self.request_reference} - {self.wallet}"


class SettlementBatch(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    beneficiary_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="finance_settlement_batches",
    )
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    period_start = models.DateField()
    period_end = models.DateField()
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    external_reference = models.CharField(max_length=120, blank=True, default="")
    payment_evidence = models.FileField(upload_to="finance/settlements/%Y/%m/", null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="prepared_finance_settlements",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_finance_settlements",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="paid_finance_settlements",
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="cancelled_finance_settlements",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default="")
    class Meta:
        ordering = ["-period_end", "-created_at"]
        indexes = [
            models.Index(fields=["beneficiary_organization", "status"], name="fin_set_org_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["external_reference"],
                condition=~models.Q(external_reference=""),
                name="fin_unique_settlement_external_ref",
            )
        ]

    def clean(self):
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": "Period end cannot precede period start."})

    def __str__(self):
        return f"Settlement {self.id or 'new'} - {self.beneficiary_organization}"


class SettlementItem(TimeStampedModel):
    batch = models.ForeignKey(
        SettlementBatch,
        on_delete=models.PROTECT,
        related_name="items",
    )
    allocation = models.ForeignKey(
        EncounterAllocation,
        on_delete=models.PROTECT,
        related_name="settlement_items",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({"amount": "Settlement amount must be greater than zero."})
        if self.batch_id and self.currency != self.batch.currency:
            raise ValidationError({"currency": "Settlement item currency must match its batch."})

    def __str__(self):
        return f"Settlement item {self.id or 'new'} - {self.amount} {self.currency}"


def service_partner_earning_reference():
    return f"SPE-{uuid.uuid4().hex[:16].upper()}"


def service_partner_payment_evidence_path(instance, filename):
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"finance/service-partner-evidence/{instance.pk or uuid.uuid4().hex}/{uuid.uuid4().hex}.{suffix}"


class ServicePartnerSettlementBatch(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    service_partner = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="service_partner_settlement_batches",
    )
    assessment_date = models.DateField()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    assessment_count = models.PositiveIntegerField()
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2)
    final_amount = models.DecimalField(max_digits=14, decimal_places=2)
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="prepared_service_partner_settlements",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="approved_service_partner_settlements",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="rejected_service_partner_settlements",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="cancelled_service_partner_settlements",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default="")
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="paid_service_partner_settlements",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_date = models.DateField(null=True, blank=True)
    external_reference = models.CharField(max_length=120, blank=True, default="")
    payment_evidence = models.FileField(
        upload_to=service_partner_payment_evidence_path, null=True, blank=True,
    )

    class Meta:
        ordering = ["-assessment_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["external_reference"], condition=~Q(external_reference=""),
                name="fin_unique_partner_payment_ref",
            ),
            models.CheckConstraint(
                condition=Q(gross_amount__gt=0) & Q(final_amount__gt=0),
                name="fin_partner_settlement_positive",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = ServicePartnerSettlementBatch.objects.get(pk=self.pk)
            if previous.status == self.Status.PAID:
                protected = (
                    "service_partner_id", "assessment_date", "currency", "assessment_count",
                    "gross_amount", "final_amount", "status", "paid_by_id", "paid_at",
                    "payment_date", "external_reference", "payment_evidence",
                )
                if any(getattr(previous, field) != getattr(self, field) for field in protected):
                    raise ValidationError("Paid service-partner settlements are immutable.")
        return super().save(*args, **kwargs)


class ServicePartnerEarning(TimeStampedModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        SETTLEMENT_PENDING = "settlement_pending", "Settlement pending"
        PAID = "paid", "Paid"
        REVERSED = "reversed", "Reversed"

    earning_reference = models.CharField(
        max_length=24, unique=True, default=service_partner_earning_reference, editable=False,
    )
    service_partner = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="service_partner_earnings",
    )
    encounter = models.OneToOneField(
        "encounters.ScreeningEncounter", on_delete=models.PROTECT,
        related_name="service_partner_earning",
    )
    financial_record = models.OneToOneField(
        EncounterFinancialRecord, on_delete=models.PROTECT,
        related_name="service_partner_earning",
    )
    service_session = models.ForeignKey(
        "encounters.AssessmentServiceSession", on_delete=models.PROTECT,
        related_name="service_partner_earnings",
    )
    assessment_date = models.DateField()
    session_reference = models.CharField(max_length=40)
    provider_type = models.CharField(max_length=30)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    rate_snapshot = models.JSONField(default=dict)
    trigger_source = models.CharField(max_length=40)
    earned_at = models.DateTimeField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.AVAILABLE)
    active_settlement = models.ForeignKey(
        ServicePartnerSettlementBatch, on_delete=models.PROTECT, null=True, blank=True,
        related_name="active_earnings",
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-assessment_date", "earning_reference"]
        indexes = [
            models.Index(fields=["service_partner", "status", "assessment_date"], name="fin_partner_earn_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="fin_partner_earning_positive"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = ServicePartnerEarning.objects.get(pk=self.pk)
            immutable = (
                "earning_reference", "service_partner_id", "encounter_id", "financial_record_id",
                "service_session_id", "assessment_date", "session_reference", "provider_type",
                "amount", "currency", "rate_snapshot", "trigger_source", "earned_at",
            )
            if any(getattr(previous, field) != getattr(self, field) for field in immutable):
                raise ValidationError("Service-partner earning source fields are immutable.")
        return super().save(*args, **kwargs)


class ServicePartnerCorrectionRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    earning = models.ForeignKey(
        ServicePartnerEarning, on_delete=models.PROTECT, related_name="correction_requests",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="requested_service_partner_corrections",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="decided_service_partner_corrections",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="fin_partner_correction_positive"),
        ]


class ServicePartnerAdjustment(TimeStampedModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Carried forward"
        SETTLEMENT_PENDING = "settlement_pending", "Settlement pending"
        APPLIED = "applied", "Applied"
        OFFSET_WITHOUT_PAYMENT = "offset_without_payment", "Offset without payment"

    correction_request = models.OneToOneField(
        ServicePartnerCorrectionRequest, on_delete=models.PROTECT, related_name="adjustment",
    )
    original_earning = models.ForeignKey(
        ServicePartnerEarning, on_delete=models.PROTECT, related_name="adjustments",
    )
    service_partner = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="service_partner_adjustments",
    )
    encounter = models.ForeignKey(
        "encounters.ScreeningEncounter", on_delete=models.PROTECT,
        related_name="service_partner_adjustments",
    )
    financial_record = models.ForeignKey(
        EncounterFinancialRecord, on_delete=models.PROTECT,
        related_name="service_partner_adjustments",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    reason = models.TextField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.AVAILABLE)
    active_settlement = models.ForeignKey(
        ServicePartnerSettlementBatch, on_delete=models.PROTECT, null=True, blank=True,
        related_name="active_adjustments",
    )
    posted_at = models.DateTimeField()
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__lt=0), name="fin_partner_adjustment_negative"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Service-partner adjustments are append-only.")
        return super().save(*args, **kwargs)


class ServicePartnerSettlementItem(TimeStampedModel):
    batch = models.ForeignKey(
        ServicePartnerSettlementBatch, on_delete=models.PROTECT, related_name="items",
    )
    earning = models.ForeignKey(
        ServicePartnerEarning, on_delete=models.PROTECT, null=True, blank=True,
        related_name="settlement_items",
    )
    adjustment = models.ForeignKey(
        ServicePartnerAdjustment, on_delete=models.PROTECT, null=True, blank=True,
        related_name="settlement_items",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "earning"], name="fin_unique_partner_settlement_item"),
            models.UniqueConstraint(fields=["batch", "adjustment"], name="fin_unique_partner_adjustment_item"),
            models.CheckConstraint(
                condition=(Q(earning__isnull=False, adjustment__isnull=True) |
                           Q(earning__isnull=True, adjustment__isnull=False)),
                name="fin_partner_item_one_source",
            ),
        ]

    def clean(self):
        if self.amount == 0:
            raise ValidationError({"amount": "Settlement item amount cannot be zero."})
        if bool(self.earning_id) == bool(self.adjustment_id):
            raise ValidationError("A settlement item requires exactly one source.")
        if self.earning_id and self.amount <= 0:
            raise ValidationError({"amount": "Earning settlement items must be positive."})
        if self.adjustment_id and self.amount >= 0:
            raise ValidationError({"amount": "Adjustment settlement items must be negative."})
        if self.batch_id and self.currency != self.batch.currency:
            raise ValidationError({"currency": "Settlement item currency must match its batch."})


def finance_action_evidence_path(instance, filename):
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"finance/action-evidence/{instance.idempotency_key}/{uuid.uuid4().hex}.{suffix}"


class FinanceActionRequest(TimeStampedModel):
    """Maker-checker request for any compensating wallet correction."""

    class ActionType(models.TextChoices):
        REFUND = "refund", "Refund"
        REVERSAL = "reversal", "Reversal"
        ADJUSTMENT = "adjustment", "Adjustment"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved and posted"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    wallet = models.ForeignKey(
        OrganizationWallet, on_delete=models.PROTECT, related_name="finance_action_requests"
    )
    financial_record = models.ForeignKey(
        EncounterFinancialRecord, on_delete=models.PROTECT, null=True, blank=True,
        related_name="finance_action_requests",
    )
    related_entry = models.ForeignKey(
        WalletLedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
        related_name="finance_action_requests",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")
    reason = models.TextField()
    external_reference = models.CharField(max_length=120)
    evidence = models.FileField(upload_to=finance_action_evidence_path, null=True, blank=True)
    idempotency_key = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="requested_finance_actions",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="decided_finance_actions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")
    posted_entry = models.OneToOneField(
        WalletLedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
        related_name="approved_finance_action",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "action_type"], name="fin_action_status_type_idx"),
            models.Index(fields=["wallet", "status"], name="fin_action_wallet_status_idx"),
        ]

    def clean(self):
        if self.amount == 0:
            raise ValidationError({"amount": "Amount cannot be zero."})
        if self.action_type in {self.ActionType.REFUND, self.ActionType.REVERSAL} and self.amount < 0:
            raise ValidationError({"amount": "Refund and reversal amounts must be positive."})
        if self.wallet_id and self.currency != self.wallet.currency:
            raise ValidationError({"currency": "Currency must match the wallet."})
        if not self.reason.strip():
            raise ValidationError({"reason": "A reason is required."})
        if not self.external_reference.strip():
            raise ValidationError({"external_reference": "An external reference is required."})
        if self.related_entry_id and self.related_entry.wallet_id != self.wallet_id:
            raise ValidationError({"related_entry": "Related entry must belong to the same wallet."})


class FinanceControlAudit(models.Model):
    """Append-only audit record for sensitive finance control activity."""

    action = models.CharField(max_length=80)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="finance_control_audits",
    )
    wallet = models.ForeignKey(
        OrganizationWallet, on_delete=models.PROTECT, null=True, blank=True,
        related_name="control_audits",
    )
    financial_record = models.ForeignKey(
        EncounterFinancialRecord, on_delete=models.PROTECT, null=True, blank=True,
        related_name="control_audits",
    )
    action_request = models.ForeignKey(
        FinanceActionRequest, on_delete=models.PROTECT, null=True, blank=True,
        related_name="audit_entries",
    )
    settlement_batch = models.ForeignKey(
        SettlementBatch, on_delete=models.PROTECT, null=True, blank=True,
        related_name="control_audits",
    )
    before_state = models.JSONField(default=dict, blank=True)
    after_state = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Finance control audit records are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Finance control audit records are immutable.")


def sponsorship_reference():
    return f"SEN-SP-{uuid.uuid4().hex[:12].upper()}"


class EncounterSponsorship(TimeStampedModel):
    class Category(models.TextChoices):
        COMPLIMENTARY = "complimentary_client_service", "Complimentary client service"
        PROMOTIONAL = "approved_promotional_screening", "Approved promotional screening"
        HARDSHIP = "hardship_support", "Hardship support"
        PROGRAMME = "approved_programme_sponsorship", "Approved programme sponsorship"
        REPLACEMENT = "correction_replacement", "Correction or replacement"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved and reserved"
        CAPTURED = "captured", "Captured"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    sponsorship_reference = models.CharField(
        max_length=32, unique=True, default=sponsorship_reference, editable=False
    )
    encounter = models.OneToOneField(
        "encounters.ScreeningEncounter", on_delete=models.PROTECT,
        related_name="finance_sponsorship",
    )
    financial_record = models.OneToOneField(
        EncounterFinancialRecord, on_delete=models.PROTECT,
        related_name="sponsorship",
    )
    sponsor_wallet = models.ForeignKey(
        OrganizationWallet, on_delete=models.PROTECT, related_name="sponsorships"
    )
    category = models.CharField(max_length=50, choices=Category.choices)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    currency = models.CharField(max_length=3, default="NGN")
    patient_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gross_service_value = models.DecimalField(max_digits=14, decimal_places=2)
    pricing_snapshot = models.JSONField(default=dict)
    allocation_snapshot = models.JSONField(default=list)
    idempotency_key = models.CharField(max_length=120, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_encounter_sponsorships",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="decided_encounter_sponsorships",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")
    reservation = models.OneToOneField(
        WalletReservation, on_delete=models.PROTECT, null=True, blank=True,
        related_name="sponsorship",
    )
    captured_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="cancelled_encounter_sponsorships",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="fin_sponsor_status_idx"),
            models.Index(fields=["sponsor_wallet", "status"], name="fin_sponsor_wallet_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(patient_amount=0), name="fin_sponsor_patient_zero"
            ),
            models.CheckConstraint(
                condition=models.Q(gross_service_value__gt=0), name="fin_sponsor_gross_positive"
            ),
        ]

    def clean(self):
        if self.patient_amount != 0:
            raise ValidationError({"patient_amount": "A sponsored patient amount must be zero."})
        if self.gross_service_value <= 0:
            raise ValidationError({"gross_service_value": "The standard service value must be positive."})
        if self.sponsor_wallet_id and not is_sentinel_treasury_organization(self.sponsor_wallet.organization):
            raise ValidationError({"sponsor_wallet": "The sponsor wallet must be an eligible Sentinel treasury wallet."})
        if self.sponsor_wallet_id and self.currency != self.sponsor_wallet.currency:
            raise ValidationError({"currency": "Currency must match the sponsor wallet."})
        if not self.reason.strip():
            raise ValidationError({"reason": "A sponsorship reason is required."})


class SponsorshipEvent(models.Model):
    sponsorship = models.ForeignKey(
        EncounterSponsorship, on_delete=models.PROTECT, related_name="events"
    )
    action = models.CharField(max_length=60)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sponsorship_events",
    )
    source_status = models.CharField(max_length=20, blank=True, default="")
    target_status = models.CharField(max_length=20)
    reason = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=160, unique=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Sponsorship events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Sponsorship events are immutable.")


def founder_expense_reference():
    return f"SEN-FE-{uuid.uuid4().hex[:12].upper()}"


def founder_expense_evidence_path(instance, filename):
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"finance/action-evidence/{instance.idempotency_key}/{uuid.uuid4().hex}.{suffix}"


class FounderFundedExpense(TimeStampedModel):
    class FundingTreatment(models.TextChoices):
        CONTRIBUTION = "founder_contribution", "Founder contribution — not repayable"
        REIMBURSABLE = "founder_reimbursable", "Amount owed to founder — reimbursable"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SETTLED = "settled", "Settled"
        CANCELLED = "cancelled", "Cancelled"

    expense_reference = models.CharField(max_length=32, unique=True, default=founder_expense_reference, editable=False)
    expense_date = models.DateField()
    category = models.CharField(max_length=40, choices=TreasuryExpenseCategory.choices)
    supplier_payee = models.CharField(max_length=180)
    description = models.TextField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")
    evidence = models.FileField(upload_to=founder_expense_evidence_path)
    funding_treatment = models.CharField(max_length=30, choices=FundingTreatment.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    idempotency_key = models.CharField(max_length=120, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_founder_expenses")
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="decided_founder_expenses")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        indexes = [models.Index(fields=["status", "expense_date"], name="fin_founder_status_date_idx")]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="fin_founder_expense_positive")]

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({"amount": "Expense amount must be positive."})
        if not self.supplier_payee.strip() or not self.description.strip():
            raise ValidationError("Supplier/payee and description are required.")
        if len(self.currency.strip()) != 3:
            raise ValidationError({"currency": "Currency must be a three-letter code."})


class FounderFundedExpenseEvent(models.Model):
    expense = models.ForeignKey(FounderFundedExpense, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=60)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="founder_expense_events")
    source_status = models.CharField(max_length=20, blank=True, default="")
    target_status = models.CharField(max_length=20)
    reason = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=160, unique=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Founder-expense events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Founder-expense events are immutable.")


def treasury_transfer_reference():
    return f"SEN-TR-{uuid.uuid4().hex[:12].upper()}"


class TreasuryTransfer(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        EXECUTED = "executed", "Execution recorded"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        REVERSED = "reversed", "Reversed"

    transfer_reference = models.CharField(
        max_length=32, unique=True, default=treasury_transfer_reference, editable=False
    )
    wallet = models.ForeignKey(
        OrganizationWallet, on_delete=models.PROTECT, related_name="treasury_transfers"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")
    category = models.CharField(max_length=40, choices=TreasuryExpenseCategory.choices)
    purpose = models.TextField()
    destination_label = models.CharField(max_length=180)
    external_reference = models.CharField(max_length=120, blank=True, default="")
    evidence = models.FileField(upload_to=finance_action_evidence_path, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    available_surplus_snapshot = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=120, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_treasury_transfers",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="decided_treasury_transfers",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="executed_treasury_transfers",
    )
    executed_at = models.DateTimeField(null=True, blank=True)
    execution_date = models.DateField(null=True, blank=True)
    ledger_entry = models.OneToOneField(
        WalletLedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
        related_name="treasury_transfer",
    )
    reversal_entry = models.OneToOneField(
        WalletLedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
        related_name="reversed_treasury_transfer",
    )
    cancellation_reason = models.TextField(blank=True, default="")
    founder_expense = models.ForeignKey(
        FounderFundedExpense, on_delete=models.PROTECT, null=True, blank=True,
        related_name="reimbursement_transfers",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="fin_transfer_status_idx"),
            models.Index(fields=["wallet", "status"], name="fin_transfer_wallet_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="fin_transfer_amount_positive"
            ),
        ]

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({"amount": "Transfer amount must be positive."})
        if self.wallet_id and not is_sentinel_treasury_organization(self.wallet.organization):
            raise ValidationError({"wallet": "Treasury transfers require an eligible Sentinel treasury wallet."})
        if self.wallet_id and self.currency != self.wallet.currency:
            raise ValidationError({"currency": "Currency must match the wallet."})
        if not self.purpose.strip() or not self.destination_label.strip():
            raise ValidationError("Purpose and destination label are required.")
        if self.founder_expense_id:
            expense = self.founder_expense
            if expense.funding_treatment != FounderFundedExpense.FundingTreatment.REIMBURSABLE:
                raise ValidationError({"founder_expense": "Founder contributions are not reimbursable."})
            if expense.status != FounderFundedExpense.Status.APPROVED:
                raise ValidationError({"founder_expense": "Founder expense must be approved before reimbursement."})
            if self.category != TreasuryExpenseCategory.FOUNDER_REIMBURSEMENT:
                raise ValidationError({"category": "Founder reimbursements require the founder-reimbursement category."})
            if self.amount != expense.amount or self.currency != expense.currency:
                raise ValidationError({"amount": "Founder reimbursement must settle the approved expense in full."})


class TreasuryTransferEvent(models.Model):
    transfer = models.ForeignKey(
        TreasuryTransfer, on_delete=models.PROTECT, related_name="events"
    )
    action = models.CharField(max_length=60)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="treasury_transfer_events",
    )
    source_status = models.CharField(max_length=20, blank=True, default="")
    target_status = models.CharField(max_length=20)
    reason = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=160, unique=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Treasury transfer events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Treasury transfer events are immutable.")
