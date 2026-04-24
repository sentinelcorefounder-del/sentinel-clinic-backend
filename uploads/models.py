from django.db import models
from django.contrib.auth.models import User
from patients.models import Patient
from encounters.models import ScreeningEncounter
import uuid


class ImageUpload(models.Model):
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

    image_upload_id = models.CharField(max_length=30, unique=True)
    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="image_uploads"
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="image_uploads"
    )
    eye_laterality = models.CharField(max_length=10, choices=LATERALITY_CHOICES)
    image_type = models.CharField(max_length=20, choices=IMAGE_TYPE_CHOICES, default="fundus")
    image_file = models.ImageField(upload_to="encounter_uploads/")
    image_quality = models.CharField(max_length=20, choices=IMAGE_QUALITY_CHOICES, default="good")
    gradable = models.BooleanField(default=True)
    retake_required = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.image_upload_id} - {self.encounter.encounter_id}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        try:
            if hasattr(self.encounter, "update_status_from_related_records"):
                self.encounter.update_status_from_related_records()
        except Exception as exc:
            print("ImageUpload post-save status update failed:", exc)


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
        related_name="ai_analysis"
    )

    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="ai_analyses"
    )

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="ai_analyses"
    )

    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    ai_status = models.CharField(max_length=20, default="pending")

    fundus_status = models.CharField(
        max_length=20,
        choices=FUNDUS_STATUS_CHOICES,
        null=True,
        blank=True
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
    ]

    LABEL_SOURCE_CHOICES = [
        ("report_auto", "Report Auto"),
        ("manual_admin", "Manual Admin"),
    ]

    label_id = models.CharField(max_length=30, unique=True, blank=True)

    image_upload = models.OneToOneField(
        ImageUpload,
        on_delete=models.CASCADE,
        related_name="dataset_label"
    )

    source_report = models.ForeignKey(
        "reports.StructuredReport",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dataset_labels"
    )

    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="dataset_labels"
    )

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="dataset_labels"
    )

    consent_confirmed = models.BooleanField(default=False)
    image_quality_label = models.CharField(max_length=50, default="good")

    dr_grade = models.CharField(max_length=50, blank=True, default="")
    maculopathy_grade = models.CharField(max_length=50, blank=True, default="")

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
        related_name="dataset_labels_created"
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