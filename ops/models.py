from django.conf import settings
from django.db import models
from referrals.models import HospitalReferral


class OpsPayment(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
        ("exception", "Exception"),
    ]

    referral = models.ForeignKey(
        HospitalReferral,
        on_delete=models.CASCADE,
        related_name="ops_payments",
    )
    payment_id = models.CharField(max_length=100, unique=True)
    patient_email = models.EmailField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    paystack_reference = models.CharField(max_length=100, blank=True)
    payment_link = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    amount_received = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    internal_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.payment_id} - {self.status}"


class OpsAuditLog(models.Model):
    ACTION_CHOICES = [
        ("hospital_created", "Hospital Created"),
        ("clinic_created", "Clinic Created"),
        ("ops_user_created", "Ops User Created"),
        ("payment_created", "Payment Created"),
        ("payment_link_generated", "Payment Link Generated"),
        ("payment_verified", "Payment Verified"),
        ("clinic_assigned", "Clinic Assigned"),
        ("report_approved", "Report Approved"),
        ("report_rejected", "Report Rejected"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ops_audit_logs",
    )
    action = models.CharField(max_length=80, choices=ACTION_CHOICES)
    entity_type = models.CharField(max_length=80, blank=True)
    entity_id = models.CharField(max_length=120, blank=True)
    entity_label = models.CharField(max_length=255, blank=True)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} - {self.entity_label}"


class OpsNotification(models.Model):
    LEVEL_CHOICES = [
        ("info", "Info"),
        ("success", "Success"),
        ("warning", "Warning"),
        ("danger", "Danger"),
    ]

    title = models.CharField(max_length=255)
    message = models.TextField(blank=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="info")

    entity_type = models.CharField(max_length=80, blank=True)
    entity_id = models.CharField(max_length=120, blank=True)
    entity_label = models.CharField(max_length=255, blank=True)

    is_read = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ops_notifications_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title