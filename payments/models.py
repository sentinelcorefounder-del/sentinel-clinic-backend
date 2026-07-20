from django.db import models
from django.utils import timezone


class PaymentTransaction(models.Model):
    class Purpose(models.TextChoices):
        WALLET_TOP_UP = "wallet_top_up", "Wallet top-up"
        ENCOUNTER_PAYMENT = "encounter_payment", "Encounter payment"

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        INITIALIZED = "initialized", "Initialized"
        VERIFIED = "verified", "Verified"
        POSTED = "posted", "Posted to finance"
        FAILED = "failed", "Failed"
        EXCEPTION = "exception", "Exception"

    provider = models.CharField(max_length=30, default="paystack")
    reference = models.CharField(max_length=120, unique=True)
    purpose = models.CharField(max_length=30, choices=Purpose.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)
    email = models.EmailField()
    currency = models.CharField(max_length=3, default="NGN")
    expected_amount = models.DecimalField(max_digits=14, decimal_places=2)
    received_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    wallet = models.ForeignKey(
        "finance.OrganizationWallet",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_transactions",
    )
    financial_record = models.ForeignKey(
        "finance.EncounterFinancialRecord",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_transactions",
    )
    authorization_url = models.URLField(blank=True, default="")
    provider_access_code = models.CharField(max_length=120, blank=True, default="")
    provider_payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    failure_reason = models.TextField(blank=True, default="")
    initialized_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "purpose"], name="payments_pa_status_ef2398_idx"),
            models.Index(fields=["created_at"], name="payments_pa_created_a98c12_idx"),
        ]

    def mark_exception(self, reason, payload=None):
        self.status = self.Status.EXCEPTION
        self.failure_reason = str(reason)
        if payload is not None:
            self.provider_payload = payload
        self.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])

    def __str__(self):
        return f"{self.reference} - {self.get_purpose_display()}"


class PaymentWebhookEvent(models.Model):
    event_key = models.CharField(max_length=180, unique=True)
    event_name = models.CharField(max_length=80)
    reference = models.CharField(max_length=120, blank=True, default="")
    payload = models.JSONField(default=dict)
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["reference", "event_name"], name="pay_wh_ref_event_idx"),
        ]

    def __str__(self):
        return self.event_key
