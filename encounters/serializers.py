
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

            # VA fields intentionally not exposed here.
            # VA is handled by StructuredReport by eye laterality.
            "diabetes_duration",
            "symptoms_notes",
            "clinical_notes",

            "iop_before_dilation_left",
            "iop_before_dilation_right",
            "iop_after_dilation_left",
            "iop_after_dilation_right",
            "dilation_drops_used",
            "dilation_notes",

            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "screening_status",
            "created_at",
            "updated_at",
        ]
