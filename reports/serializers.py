from rest_framework import serializers
from .models import StructuredReport


class StructuredReportSerializer(serializers.ModelSerializer):
    submitted_to_ops_by_display = serializers.SerializerMethodField()
    ops_reviewed_by_display = serializers.SerializerMethodField()

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

            "left_unaided_va",
            "left_corrected_va",
            "left_dr_grade",
            "left_maculopathy_grade",

            "right_unaided_va",
            "right_corrected_va",
            "right_dr_grade",
            "right_maculopathy_grade",

            "ungradable",
            "urgency_outcome",
            "recommendation",
            "next_followup_interval",
            "report_status",
            "notes",

            "submitted_to_ops_at",
            "submitted_to_ops_by",
            "submitted_to_ops_by_display",
            "ops_reviewed_at",
            "ops_reviewed_by",
            "ops_reviewed_by_display",
            "ops_review_note",
            "payout_email_sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "submitted_to_ops_at",
            "submitted_to_ops_by",
            "submitted_to_ops_by_display",
            "ops_reviewed_at",
            "ops_reviewed_by",
            "ops_reviewed_by_display",
            "ops_review_note",
            "payout_email_sent_at",
            "created_at",
            "updated_at",
        ]

    def get_submitted_to_ops_by_display(self, obj):
        user = obj.submitted_to_ops_by
        if not user:
            return ""
        return getattr(user, "username", "") or getattr(user, "email", "") or str(user)

    def get_ops_reviewed_by_display(self, obj):
        user = obj.ops_reviewed_by
        if not user:
            return ""
        return getattr(user, "username", "") or getattr(user, "email", "") or str(user)