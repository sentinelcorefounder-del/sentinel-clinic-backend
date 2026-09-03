from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.core.validators import FileExtensionValidator
from patients.models import Patient
from encounters.models import ScreeningEncounter
from organizations.models import Organization, OrganizationBranch
import uuid


def generate_image_upload_id():
    return f"IMG-{uuid.uuid4().hex.upper()[:24]}"


class ImageUpload(models.Model):
    STORAGE_KIND_CHOICES = [("public_media", "Public media"), ("private_clinical", "Private clinical")]
    DATASET_ELIGIBILITY_CHOICES = [("legacy_policy", "Legacy consent policy"), ("excluded", "Excluded"), ("approved", "Approved")]
    LATERALITY_CHOICES = [
        ("left", "Left"),
        ("right", "Right"),
    ]

    IMAGE_TYPE_CHOICES = [
        ("fundus", "Fundus"),
        ("oct", "OCT"),
        ("other", "Other"),
    ]

    IMAGE_QUALITY_CHOICES = [
        ("good", "Good"),
        ("acceptable", "Acceptable"),
        ("poor", "Poor"),
        ("ungradable", "Ungradable"),
    ]

    image_upload_id = models.CharField(max_length=30, unique=True, default=generate_image_upload_id, editable=False)
    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="image_uploads",
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="image_uploads",
    )
    eye_laterality = models.CharField(max_length=10, choices=LATERALITY_CHOICES)
    image_type = models.CharField(max_length=20, choices=IMAGE_TYPE_CHOICES, default="fundus")
    image_file = models.ImageField(upload_to="encounter_uploads/")
    storage_kind = models.CharField(max_length=24, choices=STORAGE_KIND_CHOICES, default="public_media")
    private_object_key = models.CharField(max_length=255, blank=True, default="")
    content_sha256 = models.CharField(max_length=64, blank=True, default="")
    source_format = models.CharField(max_length=10, blank=True, default="")
    pixel_width = models.PositiveIntegerField(null=True, blank=True)
    pixel_height = models.PositiveIntegerField(null=True, blank=True)
    import_source = models.CharField(max_length=30, blank=True, default="")
    asset_organization = models.ForeignKey(Organization, null=True, blank=True, on_delete=models.PROTECT, related_name="clinical_image_assets")
    asset_branch = models.ForeignKey(OrganizationBranch, null=True, blank=True, on_delete=models.PROTECT, related_name="clinical_image_assets")
    assessment_date = models.DateField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="confirmed_clinical_image_assets")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    dataset_eligibility = models.CharField(max_length=20, choices=DATASET_ELIGIBILITY_CHOICES, default="legacy_policy")
    image_quality = models.CharField(max_length=20, choices=IMAGE_QUALITY_CHOICES, default="good")
    gradable = models.BooleanField(default=True)
    retake_required = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        constraints = [
            models.UniqueConstraint(fields=["asset_organization", "content_sha256"], condition=models.Q(storage_kind="private_clinical"), name="private_image_unique_org_checksum"),
            models.CheckConstraint(condition=models.Q(storage_kind="public_media") | (models.Q(private_object_key__gt="") & models.Q(content_sha256__gt="") & models.Q(asset_organization__isnull=False) & models.Q(asset_branch__isnull=False)), name="private_image_requires_metadata"),
        ]

    def __str__(self):
        return f"{self.image_upload_id} - {self.encounter.encounter_id}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        try:
            if hasattr(self.encounter, "update_status_from_related_records"):
                self.encounter.update_status_from_related_records()
        except Exception as exc:
            print("ImageUpload post-save status update failed:", exc)

        # If a ready report already exists and an image is added/replaced later,
        # refresh dataset labels. Consent rules are still enforced by the pipeline.
        try:
            from uploads.dataset_pipeline import sync_dataset_from_report
            ready_reports = self.encounter.reports.filter(
                report_status__in=["signed_off", "submitted_to_ops", "ops_approved", "issued"]
            )
            for report in ready_reports:
                sync_dataset_from_report(report)
        except Exception as exc:
            print("ImageUpload dataset refresh failed:", exc)


class MobileTransferSession(models.Model):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("completed", "Completed"),
        ("expired", "Expired"),
        ("cancelled", "Cancelled"),
    ]

    session_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="mobile_transfer_sessions",
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="initiated_mobile_transfers",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.session_id} - {self.encounter.encounter_id}"


class PendingMobileImage(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending review"),
        ("confirmed", "Confirmed"),
        ("rejected", "Rejected"),
    ]

    session = models.ForeignKey(
        MobileTransferSession,
        on_delete=models.CASCADE,
        related_name="pending_images",
    )
    image_file = models.ImageField(
        upload_to="mobile_transfer_pending/",
        validators=[FileExtensionValidator(["jpg", "jpeg", "png"])],
    )
    staged_object_key = models.CharField(max_length=255, blank=True, default="")
    permanent_object_key = models.CharField(max_length=255, blank=True, default="")
    original_filename = models.CharField(max_length=255)
    checksum_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_mobile_images",
    )
    confirmed_upload = models.OneToOneField(
        ImageUpload,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mobile_transfer_source",
    )

    class Meta:
        ordering = ["uploaded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "checksum_sha256"],
                name="unique_mobile_image_per_session",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(image_file="")
                    | models.Q(staged_object_key__gt="")
                    | models.Q(permanent_object_key__gt="")
                ),
                name="pending_mobile_image_has_storage_identity",
            ),
        ]

    def __str__(self):
        return f"{self.original_filename} - {self.session.session_id}"


class BulkImageImport(models.Model):
    STATUS_CHOICES = [
        ("processing", "Processing"),
        ("preview", "Ready for review"),
        ("confirming", "Confirmation in progress"),
        ("confirmed", "Confirmed"),
        ("cancelled", "Cancelled"),
        ("failed", "Failed"),
        ("expired", "Expired"),
    ]

    import_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="bulk_image_imports"
    )
    branch = models.ForeignKey(
        OrganizationBranch, on_delete=models.PROTECT, related_name="bulk_image_imports"
    )
    service_session = models.ForeignKey(
        "encounters.AssessmentServiceSession",
        on_delete=models.PROTECT,
        related_name="bulk_image_imports",
    )
    archive_checksum_sha256 = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="processing")
    image_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    safe_error_code = models.CharField(max_length=80, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_bulk_image_imports"
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="confirmed_bulk_image_imports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cleanup_pending = models.BooleanField(default=False)
    confirmation_token = models.UUIDField(null=True, blank=True, editable=False)
    confirmation_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="bulk_import_unique_org_idempotency",
            ),
            models.UniqueConstraint(
                fields=["organization", "service_session", "archive_checksum_sha256"],
                condition=models.Q(status__in=["processing", "preview", "confirmed"]),
                name="bulk_import_unique_session_archive",
            ),
        ]


class BulkImageImportGroup(models.Model):
    STATUS_CHOICES = [
        ("unresolved", "Unresolved"),
        ("proposed", "Encounter proposed"),
        ("resolved", "Resolved"),
        ("invalid", "Invalid"),
    ]

    group_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    bulk_import = models.ForeignKey(
        BulkImageImport, on_delete=models.CASCADE, related_name="groups"
    )
    source_index = models.PositiveIntegerField()
    mrn = models.CharField(max_length=120, blank=True, default="")
    assessment_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="unresolved")
    proposed_encounter = models.ForeignKey(
        ScreeningEncounter, null=True, blank=True, on_delete=models.PROTECT,
        related_name="proposed_bulk_image_groups",
    )
    resolved_encounter = models.ForeignKey(
        ScreeningEncounter, null=True, blank=True, on_delete=models.PROTECT,
        related_name="resolved_bulk_image_groups",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="resolved_bulk_image_groups",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    safe_issue_code = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        ordering = ["source_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["bulk_import", "source_index"], name="bulk_import_unique_group_index"
            )
        ]


class BulkImageImportItem(models.Model):
    DECISION_CHOICES = [
        ("unresolved", "Unresolved"),
        ("left", "Left"),
        ("right", "Right"),
        ("rejected", "Rejected"),
        ("invalid", "Invalid"),
        ("skipped", "Skipped non-image"),
    ]

    item_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    group = models.ForeignKey(
        BulkImageImportGroup, on_delete=models.CASCADE, related_name="items"
    )
    source_index = models.PositiveIntegerField()
    staged_object_key = models.CharField(max_length=255, blank=True, default="")
    permanent_object_key = models.CharField(max_length=255, blank=True, default="")
    permanent_copy_status = models.CharField(max_length=20, default="none")
    permanent_cleanup_pending = models.BooleanField(default=False)
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    detected_format = models.CharField(max_length=10, blank=True, default="")
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES, default="unresolved")
    safe_issue_code = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["group__source_index", "source_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "source_index"], name="bulk_import_unique_item_index"
            ),
            models.UniqueConstraint(
                fields=["group", "checksum_sha256"],
                condition=~models.Q(checksum_sha256=""),
                name="bulk_import_unique_group_checksum",
            ),
        ]


class BulkImageAttachment(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="bulk_image_attachments")
    item = models.OneToOneField(
        BulkImageImportItem, on_delete=models.PROTECT, related_name="attachment"
    )
    image_upload = models.OneToOneField(
        ImageUpload, on_delete=models.PROTECT, related_name="bulk_import_attachment"
    )
    encounter = models.ForeignKey(
        ScreeningEncounter, on_delete=models.PROTECT, related_name="bulk_image_attachments"
    )
    eye_laterality = models.CharField(max_length=10, choices=ImageUpload.LATERALITY_CHOICES)
    checksum_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "checksum_sha256"], name="bulk_attachment_unique_org_checksum"),
            models.UniqueConstraint(
                fields=["encounter", "eye_laterality"],
                name="bulk_attachment_unique_encounter_eye",
            )
        ]


class AIAnalysis(models.Model):
    PROVIDER_CHOICES = [
        ("openai", "OpenAI"),
        ("sentinel", "Sentinel AI"),
        ("hybrid", "Hybrid AI"),
    ]

    FUNDUS_STATUS_CHOICES = [
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
        ("uncertain", "Uncertain"),
        ("error", "Error"),
    ]

    analysis_id = models.CharField(max_length=30, unique=True, blank=True)

    image_upload = models.OneToOneField(
        ImageUpload,
        on_delete=models.CASCADE,
        related_name="ai_analysis",
    )

    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="ai_analyses",
    )

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="ai_analyses",
    )

    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    ai_status = models.CharField(max_length=20, default="pending")

    fundus_status = models.CharField(
        max_length=20,
        choices=FUNDUS_STATUS_CHOICES,
        null=True,
        blank=True,
    )

    prediction = models.CharField(max_length=150, null=True, blank=True)
    referable = models.BooleanField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)

    severity = models.IntegerField(null=True, blank=True)
    severity_label = models.CharField(max_length=80, null=True, blank=True)

    image_quality = models.CharField(max_length=50, null=True, blank=True)
    risk_flag = models.CharField(max_length=80, null=True, blank=True)
    suggested_review_priority = models.CharField(max_length=80, null=True, blank=True)

    message = models.TextField(null=True, blank=True)
    draft_note = models.TextField(null=True, blank=True)
    disclaimer = models.TextField(null=True, blank=True)

    heatmap_url = models.URLField(null=True, blank=True)
    processed_image_url = models.URLField(null=True, blank=True)

    raw_response_json = models.JSONField(null=True, blank=True)
    model_version = models.CharField(max_length=100, null=True, blank=True)

    analyzed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.analysis_id:
            self.analysis_id = f"AI-{uuid.uuid4().hex[:10].upper()}"

        super().save(*args, **kwargs)

        # Critical fix:
        # AI may complete after report creation/submission.
        # Re-sync ready reports so DatasetLabel gets AI fields updated.
        try:
            from uploads.dataset_pipeline import sync_dataset_from_report
            ready_reports = self.encounter.reports.filter(
                report_status__in=["signed_off", "submitted_to_ops", "ops_approved", "issued"]
            )
            for report in ready_reports:
                sync_dataset_from_report(report)
        except Exception as exc:
            print("AIAnalysis dataset refresh failed:", exc)

    def __str__(self):
        return f"{self.analysis_id} - {self.provider}"


class DatasetLabel(models.Model):
    QUALITY_FLAG_CHOICES = [
        ("high", "High"),
        ("medium", "Medium"),
        ("low", "Low"),
    ]

    DISAGREEMENT_CHOICES = [
        ("none", "None"),
        ("ai_unavailable", "AI Unavailable"),
        ("referable_mismatch", "Referable Mismatch"),
        ("ai_missed_referable", "AI Missed Referable"),
        ("ai_overcalled_referable", "AI Overcalled Referable"),
    ]

    LABEL_SOURCE_CHOICES = [
        ("report_auto", "Report Auto"),
        ("manual_admin", "Manual Admin"),
    ]

    label_id = models.CharField(max_length=30, unique=True, blank=True)

    image_upload = models.OneToOneField(
        ImageUpload,
        on_delete=models.CASCADE,
        related_name="dataset_label",
    )

    source_report = models.ForeignKey(
        "reports.StructuredReport",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dataset_labels",
    )

    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="dataset_labels",
    )

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="dataset_labels",
    )

    consent_confirmed = models.BooleanField(default=False)
    image_quality_label = models.CharField(max_length=50, default="good")

    eye_laterality = models.CharField(max_length=10, blank=True, default="")
    unaided_visual_acuity = models.CharField(max_length=20, blank=True, default="")
    corrected_visual_acuity = models.CharField(max_length=20, blank=True, default="")

    dr_grade = models.CharField(max_length=50, blank=True, default="")
    maculopathy_grade = models.CharField(max_length=50, blank=True, default="")

    diabetic_referable = models.BooleanField(default=False)
    vision_referral_needed = models.BooleanField(default=False)
    vision_referral_reason = models.CharField(max_length=150, blank=True, default="")

    referable = models.BooleanField(default=False)
    referral_urgency = models.CharField(max_length=50, default="routine")

    clinician_notes = models.TextField(blank=True, default="")
    other_findings = models.TextField(blank=True, default="")

    ai_prediction_at_label_time = models.CharField(max_length=150, blank=True, default="")
    ai_provider_at_label_time = models.CharField(max_length=50, blank=True, default="")
    ai_confidence_at_label_time = models.FloatField(null=True, blank=True)
    ai_referable_at_label_time = models.BooleanField(null=True, blank=True)
    ai_raw_response_at_label_time = models.JSONField(null=True, blank=True)

    report_status_at_label_time = models.CharField(max_length=50, blank=True, default="")

    quality_score = models.FloatField(null=True, blank=True)
    quality_flag = models.CharField(
        max_length=20,
        choices=QUALITY_FLAG_CHOICES,
        default="medium",
    )

    ai_clinician_agreement = models.BooleanField(null=True, blank=True)
    disagreement_flag = models.CharField(
        max_length=50,
        choices=DISAGREEMENT_CHOICES,
        default="none",
    )

    label_source = models.CharField(
        max_length=30,
        choices=LABEL_SOURCE_CHOICES,
        default="report_auto",
    )

    labelled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dataset_labels_created",
    )

    labelled_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-labelled_at"]

    def save(self, *args, **kwargs):
        if not self.label_id:
            self.label_id = f"LBL-{uuid.uuid4().hex[:10].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.label_id} - {self.image_upload.image_upload_id}"
