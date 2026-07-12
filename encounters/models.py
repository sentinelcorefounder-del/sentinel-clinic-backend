from django.db import models
from patients.models import Patient
from organizations.models import Organization


class ScreeningEncounter(models.Model):
    PROGRAMME_CHOICES = [("diabetic_screening", "Diabetic Retinal Assessment")]
    SOURCE_TYPE_CHOICES = [("hospital_referral", "Hospital Referral"), ("clinic_direct", "Clinic Direct")]
    WORKFLOW_ROUTE_CHOICES = [("sentinel_managed", "Sentinel Managed"), ("clinic_managed", "Clinic Managed")]
    PAYMENT_RESPONSIBILITY_CHOICES = [("patient", "Patient"), ("clinic", "Clinic"), ("hospital", "Hospital"), ("programme", "Programme Sponsor"), ("waived", "Waived")]
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
        Patient,
        on_delete=models.CASCADE,
        related_name="encounters",
    )

    encounter_date = models.DateField()
    encounter_type = models.CharField(max_length=50, default="retinal_assessment")

    programme = models.CharField(max_length=40, choices=PROGRAMME_CHOICES, default="diabetic_screening")
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPE_CHOICES, default="hospital_referral")
    workflow_route = models.CharField(max_length=30, choices=WORKFLOW_ROUTE_CHOICES, default="sentinel_managed")
    payment_responsibility = models.CharField(max_length=20, choices=PAYMENT_RESPONSIBILITY_CHOICES, default="hospital")
    originating_organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name="originated_screening_encounters")
    hospital_referral = models.ForeignKey("referrals.HospitalReferral", on_delete=models.SET_NULL, null=True, blank=True, related_name="screening_encounters")

    screening_status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="scheduled",
    )

    # Legacy fields retained for older rows. Do not use in active UI.
    visual_acuity_left = models.CharField(max_length=20, blank=True)
    visual_acuity_right = models.CharField(max_length=20, blank=True)

    # VA should be captured by technicians on the encounter.
    left_unaided_va = models.CharField(max_length=20, blank=True)
    right_unaided_va = models.CharField(max_length=20, blank=True)

    left_corrected_pinhole_va = models.CharField(max_length=20, blank=True)
    right_corrected_pinhole_va = models.CharField(max_length=20, blank=True)

    left_va_method = models.CharField(
        max_length=20,
        choices=VA_METHOD_CHOICES,
        blank=True,
        default="",
    )
    right_va_method = models.CharField(
        max_length=20,
        choices=VA_METHOD_CHOICES,
        blank=True,
        default="",
    )

    # Clinical encounter fields
    diabetes_duration = models.CharField(max_length=50, blank=True)
    symptoms_notes = models.TextField(blank=True)
    clinical_notes = models.TextField(blank=True)

    # IOP / dilation fields
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

    def update_status_from_related_records(self):
        if self.screening_status == "cancelled":
            return

        has_uploads = self.image_uploads.exists()
        has_reports = self.reports.exists()
        has_completed_report = self.reports.filter(
            report_status__in=["issued", "submitted_to_ops", "ops_approved"]
        ).exists()

        if has_completed_report:
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