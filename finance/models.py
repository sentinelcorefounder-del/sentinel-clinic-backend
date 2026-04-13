from django.db import models
from organizations.models import Organization
from referrals.models import Referral
from appointments.models import Appointment
from payments.models import Payment


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PricingRule(TimeStampedModel):
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pricing_rules",
    )
    referral_type = models.CharField(max_length=100, blank=True, null=True)
    service_type = models.CharField(max_length=100, blank=True, null=True)
    currency = models.CharField(max_length=10, default="NGN")
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    hospital_commission_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    clinic_payout_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sentinel_retained_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    effective_from = models.DateField(blank=True, null=True)
    effective_to = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class PayoutLedger(TimeStampedModel):
    class EntryType(models.TextChoices):
        HOSPITAL_COMMISSION = "hospital_commission", "Hospital Commission"
        CLINIC_PAYOUT = "clinic_payout", "Clinic Payout"
        SENTINEL_RETAINED = "sentinel_retained", "Sentinel Retained"

    class LedgerStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DISBURSED = "disbursed", "Disbursed"
        CANCELLED = "cancelled", "Cancelled"

    ledger_code = models.CharField(max_length=50, unique=True)
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="ledger_entries")
    referral = models.ForeignKey(Referral, on_delete=models.CASCADE, related_name="ledger_entries")
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    entry_type = models.CharField(max_length=50, choices=EntryType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    status = models.CharField(
        max_length=20,
        choices=LedgerStatus.choices,
        default=LedgerStatus.PENDING,
    )
    disbursed_at = models.DateTimeField(blank=True, null=True)
    disbursement_reference = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.ledger_code