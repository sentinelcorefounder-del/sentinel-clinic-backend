from decimal import Decimal
import uuid

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
    notes = models.TextField(blank=True, default="")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_finance_settlements",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-period_end", "-created_at"]
        indexes = [
            models.Index(fields=["beneficiary_organization", "status"], name="fin_set_org_status_idx"),
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
    allocation = models.OneToOneField(
        EncounterAllocation,
        on_delete=models.PROTECT,
        related_name="settlement_item",
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
