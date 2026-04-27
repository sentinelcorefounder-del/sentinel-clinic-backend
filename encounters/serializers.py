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
            # VA fields intentionally not exposed in normal encounter UI/API.
            # They remain in the model only for backwards compatibility.
            "diabetes_duration",
            "symptoms_notes",
            "clinical_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "screening_status",
            "created_at",
            "updated_at",
        ]
