from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.conf import settings
from patients.models import Patient
from encounters.models import ScreeningEncounter


class StructuredReport(models.Model):
    REPORT_OWNER_CHOICES = [("clinic", "Clinic"), ("sentinel", "Sentinel")]
    REPORT_STATUS_CHOICES = [
        ("draft", "Draft"),
        ("under_review", "Under Review"),
        ("signed_off", "Signed Off"),
        ("submitted_to_ops", "Submitted to Ops"),
        ("returned_to_clinic", "Returned to Clinic"),
        ("ops_approved", "Ops Approved"),
        ("ops_rejected", "Ops Rejected"),
        ("issued", "Issued"),
        ("clinic_signed", "Clinic Signed"),
        ("clinic_issued", "Clinic Issued"),
    ]

    DISTRIBUTION_STATUS_CHOICES = [
        ("not_ready", "Not Ready"),
        ("awaiting_distribution", "Awaiting Distribution"),
        ("released_to_hospital", "Released to Hospital"),
        ("completed", "Completed"),
    ]

    RECALL_STATUS_CHOICES = [
        ("not_set", "Not Set"),
        ("scheduled", "Scheduled"),
        ("due_soon", "Due Soon"),
        ("due", "Due"),
        ("overdue", "Overdue"),
        ("contacted", "Contacted"),
        ("booked", "Booked"),
        ("completed", "Completed"),
        ("deferred", "Deferred"),
    ]

    URGENCY_OUTCOME_CHOICES = [
        ("routine_followup", "Routine Follow-up"),
        ("early_review", "Early Review"),
        ("urgent_referral", "Urgent Referral"),
        ("ophthalmology_required", "Ophthalmology Required"),
        ("image_retake", "Image Retake"),
    ]

    DR_GRADE_CHOICES = [
        ("", "Not Recorded"),
        ("R0", "R0 - No DR"),
        ("R1", "R1 - Background DR"),
        ("R2", "R2 - Pre-proliferative DR"),
        ("R3A", "R3A - Active proliferative DR"),
        ("R3S", "R3S - Stable treated proliferative DR"),
        ("U", "Ungradable"),
    ]

    MACULOPATHY_GRADE_CHOICES = [
        ("", "Not Recorded"),
        ("M0", "M0 - No maculopathy"),
        ("M1", "M1 - Maculopathy"),
        ("U", "Ungradable"),
    ]

    VA_CHOICES = [
        ("", "Not Recorded"),
        ("6/4", "6/4"),
        ("6/5", "6/5"),
        ("6/6", "6/6"),
        ("6/7.5", "6/7.5"),
        ("6/9", "6/9"),
        ("6/12", "6/12"),
        ("6/15", "6/15"),
        ("6/18", "6/18"),
        ("6/24", "6/24"),
        ("6/36", "6/36"),
        ("6/60", "6/60"),
        ("CF", "Counting Fingers"),
        ("HM", "Hand Movements"),
        ("PL", "Perception of Light"),
        ("NPL", "No Perception of Light"),
    ]

    report_id = models.CharField(max_length=30, unique=True)

    encounter = models.OneToOneField(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="structured_report",
    )

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="reports",
    )

    review_date = models.DateField()

    # Legacy fields retained only so old DB rows/migrations do not break.
    # Do not use these in the active UI/PDF/dataset logic.
    dr_grade = models.CharField(max_length=50, blank=True)
    maculopathy_grade = models.CharField(max_length=50, blank=True)

    left_unaided_va = models.CharField(max_length=20, choices=VA_CHOICES, blank=True)
    left_corrected_va = models.CharField(max_length=20, choices=VA_CHOICES, blank=True)
    left_dr_grade = models.CharField(max_length=20, choices=DR_GRADE_CHOICES, blank=True)
    left_maculopathy_grade = models.CharField(max_length=20, choices=MACULOPATHY_GRADE_CHOICES, blank=True)

    right_unaided_va = models.CharField(max_length=20, choices=VA_CHOICES, blank=True)
    right_corrected_va = models.CharField(max_length=20, choices=VA_CHOICES, blank=True)
    right_dr_grade = models.CharField(max_length=20, choices=DR_GRADE_CHOICES, blank=True)
    right_maculopathy_grade = models.CharField(max_length=20, choices=MACULOPATHY_GRADE_CHOICES, blank=True)

    ungradable = models.BooleanField(default=False)

    urgency_outcome = models.CharField(
        max_length=50,
        choices=URGENCY_OUTCOME_CHOICES,
        default="routine_followup",
    )

    recommendation = models.TextField(blank=True)
    # Legacy free-text field retained for compatibility.
    next_followup_interval = models.CharField(max_length=50, blank=True)

    recall_months = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(24)],
    )
    recall_due_date = models.DateField(null=True, blank=True, db_index=True)
    recall_status = models.CharField(
        max_length=20,
        choices=RECALL_STATUS_CHOICES,
        default="not_set",
    )
    recall_contacted_at = models.DateTimeField(null=True, blank=True)
    recall_booked_at = models.DateTimeField(null=True, blank=True)
    recall_completed_at = models.DateTimeField(null=True, blank=True)
    recall_note = models.TextField(blank=True, default="")

    generated_clinical_summary = models.TextField(blank=True, default="")
    final_clinical_summary = models.TextField(blank=True, default="")
    clinical_summary_overridden = models.BooleanField(default=False)
    report_layout = models.CharField(
        max_length=20,
        choices=[("text_only", "Text only"), ("with_investigations", "With investigations")],
        default="text_only",
    )
    selected_fundus_upload_ids = models.JSONField(default=list, blank=True)
    selected_ocular_investigation_ids = models.JSONField(default=list, blank=True)
    attachment_captions = models.JSONField(default=dict, blank=True)

    report_status = models.CharField(
        max_length=30,
        choices=REPORT_STATUS_CHOICES,
        default="draft",
    )

    notes = models.TextField(blank=True)

    submitted_to_ops_at = models.DateTimeField(null=True, blank=True)
    submitted_to_ops_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports_submitted_to_ops",
    )

    ops_reviewed_at = models.DateTimeField(null=True, blank=True)
    ops_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports_reviewed_by_ops",
    )

    ops_review_note = models.TextField(blank=True, default="")
    return_reason = models.TextField(blank=True, default="")
    resubmission_count = models.PositiveIntegerField(default=0)
    issued_at = models.DateTimeField(null=True, blank=True)
    hospital_viewed_at = models.DateTimeField(null=True, blank=True)
    hospital_downloaded_at = models.DateTimeField(null=True, blank=True)
    payout_email_sent_at = models.DateTimeField(null=True, blank=True)

    report_owner = models.CharField(max_length=20, choices=REPORT_OWNER_CHOICES, default="sentinel")
    signed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports_signed")
    signed_at = models.DateTimeField(null=True, blank=True)
    signer_name = models.CharField(max_length=255, blank=True, default="")
    signer_role = models.CharField(max_length=255, blank=True, default="")
    signer_registration_number = models.CharField(max_length=120, blank=True, default="")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports_issued")
    sentinel_archive_received_at = models.DateTimeField(null=True, blank=True)

    distribution_status = models.CharField(
        max_length=40,
        choices=DISTRIBUTION_STATUS_CHOICES,
        default="not_ready",
    )
    hospital_released_at = models.DateTimeField(null=True, blank=True)
    hospital_released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports_released_to_hospital",
    )
    patient_delivery_required = models.BooleanField(default=False)
    patient_delivered_at = models.DateTimeField(null=True, blank=True)

    lock_version = models.PositiveIntegerField(default=0)
    submitted_version = models.ForeignKey(
        "StructuredReportVersion", null=True, blank=True, on_delete=models.PROTECT,
        related_name="submitted_reports",
    )
    issued_version = models.ForeignKey(
        "StructuredReportVersion", null=True, blank=True, on_delete=models.PROTECT,
        related_name="issued_reports",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-review_date", "-created_at"]

    def __str__(self):
        return f"{self.report_id} - {self.encounter.encounter_id}"

    def save(self, *args, **kwargs):
        # Report creation should not remain draft in the active clinical workflow.
        # As soon as a report is created/edited, it is under clinical review unless it has moved further.
        if self.report_status == "draft":
            self.report_status = "under_review"

        super().save(*args, **kwargs)

        try:
            self.encounter.update_status_from_related_records()
        except Exception as exc:
            print("StructuredReport encounter status update failed:", exc)

        try:
            from uploads.dataset_pipeline import sync_dataset_from_report
            sync_dataset_from_report(self)
        except Exception as exc:
            print("StructuredReport dataset sync failed:", exc)


class PatientReportDelivery(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]

    CHANNEL_CHOICES = [
        ("email", "Email"),
    ]

    report = models.ForeignKey(
        StructuredReport,
        on_delete=models.CASCADE,
        related_name="patient_deliveries",
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="report_deliveries",
    )
    channel = models.CharField(
        max_length=20,
        choices=CHANNEL_CHOICES,
        default="email",
    )
    recipient = models.EmailField()
    include_images = models.BooleanField(default=False)
    consent_confirmed = models.BooleanField(default=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="patient_report_deliveries_requested",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="patient_report_deliveries_sent",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    failure_reason = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.report.report_id} -> {self.recipient}"


class ReportStatusEvent(models.Model):
    EVENT_CHOICES = [
        ("created", "Created"),
        ("responsibility_accepted", "Clinical responsibility accepted"),
        ("responsibility_taken_over", "Clinical responsibility taken over"),
        ("submitted_to_ops", "Submitted to Ops"),
        ("returned_to_clinic", "Returned to Clinic"),
        ("resubmitted", "Resubmitted"),
        ("rejected", "Rejected"),
        ("issued", "Issued"),
        ("clinic_signed", "Clinic Signed"),
        ("clinic_issued", "Clinic Issued"),
        ("queued_for_distribution", "Queued for Distribution"),
        ("released_to_hospital", "Released to Hospital"),
        ("hospital_viewed", "Hospital Viewed"),
        ("hospital_downloaded", "Hospital Downloaded"),
    ]

    report = models.ForeignKey(
        StructuredReport,
        on_delete=models.CASCADE,
        related_name="status_events",
    )
    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES)
    from_status = models.CharField(max_length=30, blank=True, default="")
    to_status = models.CharField(max_length=30, blank=True, default="")
    note = models.TextField(blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="report_status_events",
    )
    source_version = models.ForeignKey(
        "StructuredReportVersion", null=True, blank=True, on_delete=models.PROTECT,
        related_name="source_status_events",
    )
    target_version = models.ForeignKey(
        "StructuredReportVersion", null=True, blank=True, on_delete=models.PROTECT,
        related_name="target_status_events",
    )
    authority_used = models.CharField(max_length=80, blank=True, default="")
    correction_note = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="report_unique_status_event_idempotency",
            )
        ]

    def __str__(self):
        return f"{self.report.report_id} - {self.event_type}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Report status events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Report status events cannot be deleted.")


class ReportClinicalResponsibility(models.Model):
    report = models.OneToOneField(
        StructuredReport, on_delete=models.PROTECT, related_name="clinical_responsibility"
    )
    current_clinician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="responsible_retinal_reports",
    )
    original_clinician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="originally_responsible_retinal_reports",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="retinal_responsibility_acceptances",
    )
    accepted_at = models.DateTimeField()
    authority_used = models.CharField(max_length=40)
    clinician_name = models.CharField(max_length=255)
    professional_role = models.CharField(max_length=120)
    registration_number = models.CharField(max_length=120)
    clinic = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="retinal_report_responsibilities",
    )
    branch = models.ForeignKey(
        "organizations.OrganizationBranch", on_delete=models.PROTECT,
        related_name="retinal_report_responsibilities",
    )
    previous_clinician = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="transferred_retinal_reports",
    )
    takeover_reason = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)


class StructuredReportVersion(models.Model):
    PURPOSE_CHOICES = [
        ("legacy_baseline", "Legacy baseline"),
        ("initial", "Initial clinical version"),
        ("clinical_edit", "Clinical edit"),
        ("returned_correction", "Returned correction"),
    ]

    report = models.ForeignKey(
        StructuredReport, on_delete=models.PROTECT, related_name="versions"
    )
    version_number = models.PositiveIntegerField()
    snapshot_schema_version = models.PositiveSmallIntegerField(default=1)
    clinical_snapshot = models.JSONField(default=dict)
    checksum_sha256 = models.CharField(max_length=64)
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="retinal_report_versions",
    )
    responsibility_snapshot = models.JSONField(default=dict, blank=True)
    purpose = models.CharField(max_length=30, choices=PURPOSE_CHOICES)
    correction_note = models.TextField(blank=True, default="")
    source_version = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="derived_versions",
    )
    pdf_object_key = models.CharField(max_length=500, blank=True, default="")
    pdf_checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    pdf_size = models.PositiveBigIntegerField(null=True, blank=True)
    pdf_generated_at = models.DateTimeField(null=True, blank=True)
    legacy_pdf_unbound = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "version_number"],
                name="report_unique_clinical_version",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Clinical report versions are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Clinical report versions cannot be deleted.")


class EyeHealthScreeningReport(models.Model):
    class Outcome(models.TextChoices):
        NO_IMMEDIATE_CONCERN = "no_immediate_concern", "No immediate concern identified"
        ROUTINE_EXAM = "routine_eye_examination", "Routine eye examination advised"
        FURTHER_ASSESSMENT = "further_assessment", "Further assessment recommended"
        URGENT_OPHTHALMOLOGY = "urgent_ophthalmology", "Urgent ophthalmology assessment recommended"
        INCONCLUSIVE = "inconclusive_repeat", "Inconclusive — repeat testing required"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALIZED = "finalized", "Finalized"

    encounter = models.OneToOneField(
        ScreeningEncounter, on_delete=models.PROTECT, related_name="eye_health_report"
    )
    outcome = models.CharField(max_length=40, choices=Outcome.choices, blank=True, default="")
    selected_advice = models.JSONField(default=list, blank=True)
    advice = models.TextField(blank=True, default="")
    right_visual_field_result = models.TextField(blank=True, default="")
    left_visual_field_result = models.TextField(blank=True, default="")
    right_fundus_result = models.TextField(blank=True, default="")
    left_fundus_result = models.TextField(blank=True, default="")
    selected_fundus_upload_ids = models.JSONField(default=list, blank=True)
    selected_visual_field_investigation_ids = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    preview_checksum = models.CharField(max_length=64, blank=True, default="")
    previewed_at = models.DateTimeField(null=True, blank=True)
    lock_version = models.PositiveIntegerField(default=1)
    finalized_version = models.OneToOneField(
        "EyeHealthScreeningReportVersion", on_delete=models.PROTECT,
        null=True, blank=True, related_name="finalized_for_report",
    )
    correction_reason = models.TextField(blank=True, default="")
    correction_source_version = models.ForeignKey(
        "EyeHealthScreeningReportVersion", on_delete=models.PROTECT,
        null=True, blank=True, related_name="correction_drafts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class EyeHealthScreeningReportVersion(models.Model):
    report = models.ForeignKey(
        EyeHealthScreeningReport, on_delete=models.PROTECT, related_name="versions"
    )
    version_number = models.PositiveIntegerField()
    clinical_snapshot = models.JSONField(default=dict)
    checksum_sha256 = models.CharField(max_length=64)
    clinician_snapshot = models.JSONField(default=dict)
    attachment_manifest = models.JSONField(default=list, blank=True)
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="eye_health_report_versions",
    )
    purpose = models.CharField(
        max_length=20, choices=[("initial", "Initial"), ("correction", "Correction")],
        default="initial",
    )
    correction_note = models.TextField(blank=True, default="")
    source_version = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="corrected_versions",
    )
    pdf_object_key = models.CharField(max_length=500, blank=True, default="")
    pdf_checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    pdf_size = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "version_number"], name="eye_health_unique_version"
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Eye-health screening report versions are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Eye-health screening report versions cannot be deleted.")
