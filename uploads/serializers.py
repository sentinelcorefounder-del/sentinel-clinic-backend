from rest_framework import serializers
from .models import ImageUpload, AIAnalysis


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