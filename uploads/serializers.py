from rest_framework import serializers
from .models import ImageUpload, AIAnalysis, DatasetLabel


class AIAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIAnalysis
        fields = [
            "id",
            "analysis_id",
            "provider",
            "ai_status",
            "fundus_status",
            "prediction",
            "referable",
            "confidence",
            "severity",
            "severity_label",
            "image_quality",
            "risk_flag",
            "suggested_review_priority",
            "message",
            "draft_note",
            "disclaimer",
            "heatmap_url",
            "processed_image_url",
            "model_version",
            "analyzed_at",
            "created_at",
        ]


class DatasetLabelSerializer(serializers.ModelSerializer):
    labelled_by_username = serializers.CharField(
        source="labelled_by.username",
        read_only=True
    )

    class Meta:
        model = DatasetLabel
        fields = [
            "id",
            "label_id",
            "image_upload",
            "source_report",
            "encounter",
            "patient",
            "consent_confirmed",
            "image_quality_label",
            "dr_grade",
            "maculopathy_grade",
            "referable",
            "referral_urgency",
            "clinician_notes",
            "other_findings",
            "ai_prediction_at_label_time",
            "ai_provider_at_label_time",
            "ai_confidence_at_label_time",
            "ai_referable_at_label_time",
            "report_status_at_label_time",
            "quality_score",
            "quality_flag",
            "ai_clinician_agreement",
            "disagreement_flag",
            "label_source",
            "labelled_by",
            "labelled_by_username",
            "labelled_at",
            "created_at",
        ]


class ImageUploadSerializer(serializers.ModelSerializer):
    image_file = serializers.ImageField(use_url=True)
    ai_analysis = AIAnalysisSerializer(read_only=True)

    class Meta:
        model = ImageUpload
        fields = [
            "id",
            "image_upload_id",
            "encounter",
            "patient",
            "eye_laterality",
            "image_type",
            "image_file",
            "image_quality",
            "gradable",
            "retake_required",
            "uploaded_at",
            "ai_analysis",
        ]