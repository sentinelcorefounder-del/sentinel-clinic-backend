from rest_framework import serializers
from .models import ImageUpload, AIAnalysis, DatasetLabel


class AIAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIAnalysis
        fields = [
            "id", "analysis_id", "provider", "ai_status", "fundus_status",
            "prediction", "referable", "confidence", "severity", "severity_label",
            "image_quality", "risk_flag", "suggested_review_priority", "message",
            "draft_note", "disclaimer", "heatmap_url", "processed_image_url",
            "model_version", "analyzed_at", "created_at",
        ]


class DatasetLabelSerializer(serializers.ModelSerializer):
    labelled_by_username = serializers.CharField(source="labelled_by.username", read_only=True)

    class Meta:
        model = DatasetLabel
        fields = [
            "id", "label_id", "image_upload", "source_report", "encounter", "patient",
            "consent_confirmed", "image_quality_label", "eye_laterality",
            "unaided_visual_acuity", "corrected_visual_acuity", "dr_grade",
            "maculopathy_grade", "diabetic_referable", "vision_referral_needed",
            "vision_referral_reason", "referable", "referral_urgency",
            "clinician_notes", "other_findings", "ai_prediction_at_label_time",
            "ai_provider_at_label_time", "ai_confidence_at_label_time",
            "ai_referable_at_label_time", "report_status_at_label_time",
            "quality_score", "quality_flag", "ai_clinician_agreement",
            "disagreement_flag", "label_source", "labelled_by",
            "labelled_by_username", "labelled_at", "created_at",
        ]
        read_only_fields = fields


class ImageUploadSerializer(serializers.ModelSerializer):
    image_file = serializers.ImageField(use_url=True)
    ai_analysis = AIAnalysisSerializer(read_only=True)
    dataset_label = DatasetLabelSerializer(read_only=True)
    patient_display = serializers.SerializerMethodField()
    encounter_display = serializers.SerializerMethodField()

    class Meta:
        model = ImageUpload
        fields = [
            "id", "image_upload_id", "encounter", "encounter_display", "patient",
            "patient_display", "eye_laterality", "image_type", "image_file",
            "image_quality", "gradable", "retake_required", "uploaded_at",
            "ai_analysis", "dataset_label",
        ]
        read_only_fields = ["uploaded_at", "ai_analysis", "dataset_label", "patient_display", "encounter_display"]

    def get_patient_display(self, obj):
        patient = obj.patient
        if not patient:
            return ""
        return f"{patient.patient_id} - {patient.first_name} {patient.last_name}".strip()

    def get_encounter_display(self, obj):
        encounter = obj.encounter
        if not encounter:
            return ""
        return encounter.encounter_id

    def validate(self, attrs):
        encounter = attrs.get("encounter") or getattr(self.instance, "encounter", None)
        patient = attrs.get("patient") or getattr(self.instance, "patient", None)

        if encounter and patient and encounter.patient_id != patient.id:
            raise serializers.ValidationError("Selected patient does not match the encounter patient.")

        if encounter and not patient:
            attrs["patient"] = encounter.patient

        return attrs
