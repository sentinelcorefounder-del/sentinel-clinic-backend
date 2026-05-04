from django.db import models
from django.utils import timezone
from organizations.models import Organization
from patients.models import Patient


def generate_referral_id():
    year = timezone.now().year
    prefix = f"SNT-REF-{year}-"

    latest = (
        HospitalReferral.objects.filter(referral_id__startswith=prefix)
        .order_by("-id")
        .first()
    )

    if not latest:
        next_number = 1
    else:
        try:
            next_number = int(str(latest.referral_id).split("-")[-1]) + 1
        except Exception:
            next_number = latest.id + 1

    return f"{prefix}{str(next_number).zfill(6)}"


class HospitalReferral(models.Model):
    STATUS_CHOICES = [
        ("submitted", "Submitted"),
        ("clinic_matched", "Clinic Matched"),
        ("in_clinic", "In Clinic"),
        ("report_issued", "Report Issued"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    PAYOUT_STATUS_CHOICES = [
        ("not_due", "Not Due"),
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("paid", "Paid"),
    ]

    referral_id = models.CharField(
        max_length=60,
        unique=True,
        default=generate_referral_id,
    )

    source_hospital = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_hospital_referrals",
    )

    patient = models.ForeignKey(
        Patient,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hospital_referrals",
    )

    patient_id_text = models.CharField(max_length=50, blank=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    dob = models.DateField(null=True, blank=True)
    patient_sex = models.CharField(max_length=30, blank=True)
    hospital_mrn = models.CharField(max_length=100, blank=True)
    diabetes_type = models.CharField(max_length=50, blank=True)
    reason_for_referral = models.TextField()
    phone_number = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)

    matched_clinic = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matched_hospital_referrals",
    )

    report = models.ForeignKey(
        "reports.StructuredReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hospital_referrals",
    )

    referral_date = models.DateTimeField(null=True, blank=True)

    referral_status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="submitted",
    )

    report_ready = models.BooleanField(default=False)

    hospital_commission_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    payout_status = models.CharField(
        max_length=30,
        choices=PAYOUT_STATUS_CHOICES,
        default="not_due",
    )

    payout_date = models.DateTimeField(null=True, blank=True)

    baserow_row_id = models.IntegerField(null=True, blank=True)
    source_system = models.CharField(max_length=50, default="hospital_portal")
    notes = models.TextField(blank=True)
    submitted_by_username = models.CharField(max_length=150, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referral_id} - {self.first_name} {self.last_name}"

    @property
    def patient_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()