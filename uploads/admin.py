from django.contrib import admin
from .models import ImageUpload, AIAnalysis, DatasetLabel


@admin.register(ImageUpload)
class ImageUploadAdmin(admin.ModelAdmin):
    list_display = (
        "image_upload_id",
        "encounter",
        "patient",
        "eye_laterality",
        "image_type",
        "image_quality",
        "uploaded_at",
    )
    search_fields = ("image_upload_id", "encounter__encounter_id", "patient__patient_id")
    list_filter = ("eye_laterality", "image_type", "image_quality", "gradable", "retake_required")


@admin.register(AIAnalysis)
class AIAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        "analysis_id",
        "provider",
        "ai_status",
        "prediction",
        "confidence",
        "patient",
        "created_at",
    )
    search_fields = ("analysis_id", "patient__patient_id", "image_upload__image_upload_id")
    list_filter = ("provider", "ai_status", "fundus_status", "risk_flag")


@admin.register(DatasetLabel)
class DatasetLabelAdmin(admin.ModelAdmin):
    list_display = (
        "label_id",
        "patient",
        "dr_grade",
        "maculopathy_grade",
        "referable",
        "quality_score",
        "quality_flag",
        "ai_clinician_agreement",
        "disagreement_flag",
        "labelled_at",
    )
    search_fields = (
        "label_id",
        "patient__patient_id",
        "image_upload__image_upload_id",
        "source_report__report_id",
    )
    list_filter = (
        "referable",
        "quality_flag",
        "ai_clinician_agreement",
        "disagreement_flag",
        "label_source",
        "consent_confirmed",
    )
    readonly_fields = (
        "label_id",
        "created_at",
        "labelled_at",
        "ai_raw_response_at_label_time",
    )