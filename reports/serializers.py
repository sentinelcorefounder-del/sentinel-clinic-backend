from rest_framework import serializers
from .models import StructuredReport


class StructuredReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = StructuredReport
        fields = [
            "id",
            "report_id",
            "encounter",
            "patient",
            "review_date",
            "dr_grade",
            "maculopathy_grade",
            "ungradable",
            "urgency_outcome",
            "recommendation",
            "next_followup_interval",
            "report_status",
            "notes",
            "created_at",
            "updated_at",
        ]