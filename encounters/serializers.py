from rest_framework import serializers
from .models import ScreeningEncounter


class ScreeningEncounterSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScreeningEncounter
        fields = [
            "id",
            "encounter_id",
            "patient",
            "encounter_date",
            "encounter_type",
            "screening_status",
            "visual_acuity_left",
            "visual_acuity_right",
            "diabetes_duration",
            "symptoms_notes",
            "clinical_notes",
            "created_at",
            "updated_at",
        ]