from rest_framework import serializers
from .models import StructuredReport, ReportStatusEvent, PatientReportDelivery


class ReportStatusEventSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()

    class Meta:
        model = ReportStatusEvent
        fields = [
            "id",
            "event_type",
            "from_status",
            "to_status",
            "note",
            "actor",
            "actor_display",
            "created_at",
        ]

    def get_actor_display(self, obj):
        if not obj.actor:
            return "System"
        return obj.actor.get_full_name() or obj.actor.username or obj.actor.email


class StructuredReportSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()
    sentinel_patient_id = serializers.SerializerMethodField()
    patient_id = serializers.CharField(source="patient.patient_id", read_only=True)
    submitted_to_ops_by_display = serializers.SerializerMethodField()
    ops_reviewed_by_display = serializers.SerializerMethodField()
    signed_by_display = serializers.SerializerMethodField()
    issued_by_display = serializers.SerializerMethodField()
    workflow_route = serializers.CharField(
        source="encounter.workflow_route",
        read_only=True,
    )
    source_type = serializers.CharField(
        source="encounter.source_type",
        read_only=True,
    )
    status_events = ReportStatusEventSerializer(many=True, read_only=True)

    class Meta:
        model = StructuredReport
        fields = [
            "id",
            "report_id",
            "encounter",
            "patient",
            "patient_id",
            "sentinel_patient_id",
            "patient_name",
            "review_date",
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

            "recall_months",
            "recall_due_date",
            "recall_status",
            "recall_contacted_at",
            "recall_booked_at",
            "recall_completed_at",
            "recall_note",
            "generated_clinical_summary",
            "final_clinical_summary",
            "clinical_summary_overridden",
            "report_status",
            "notes",
            "submitted_to_ops_at",
            "submitted_to_ops_by",
            "submitted_to_ops_by_display",
            "ops_reviewed_at",
            "ops_reviewed_by",
            "ops_reviewed_by_display",
            "ops_review_note",
            "return_reason",
            "resubmission_count",
            "issued_at",
            "hospital_viewed_at",
            "hospital_downloaded_at",
            "payout_email_sent_at",
            "report_owner",
            "workflow_route",
            "source_type",
            "signed_by",
            "signed_by_display",
            "signed_at",
            "signer_name",
            "signer_role",
            "signer_registration_number",
            "issued_by",
            "issued_by_display",
            "sentinel_archive_received_at",
            "distribution_status",
            "hospital_released_at",
            "hospital_released_by",
            "patient_delivery_required",
            "patient_delivered_at",
            "status_events",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "report_status",
            "submitted_to_ops_at",
            "submitted_to_ops_by",
            "submitted_to_ops_by_display",
            "ops_reviewed_at",
            "ops_reviewed_by",
            "ops_reviewed_by_display",
            "ops_review_note",
            "return_reason",
            "resubmission_count",
            "issued_at",
            "hospital_viewed_at",
            "hospital_downloaded_at",
            "payout_email_sent_at",
            "report_owner",
            "workflow_route",
            "source_type",
            "signed_by",
            "signed_by_display",
            "signed_at",
            "issued_by",
            "issued_by_display",
            "sentinel_archive_received_at",
            "hospital_released_at",
            "hospital_released_by",
            "patient_delivered_at",
            "status_events",
            "created_at",
            "updated_at",
        ]


    def get_patient_name(self, obj):
        patient = obj.patient
        return f"{patient.first_name} {patient.last_name}".strip() if patient else ""

    def get_sentinel_patient_id(self, obj):
        patient = obj.patient
        master_patient = getattr(patient, "master_patient", None) if patient else None
        return getattr(master_patient, "sentinel_patient_id", "") or getattr(patient, "sentinel_patient_id", "") or ""

    def validate(self, attrs):
        encounter = attrs.get("encounter") or getattr(self.instance, "encounter", None)
        patient = attrs.get("patient") or getattr(self.instance, "patient", None)

        if encounter and patient and encounter.patient_id != patient.id:
            raise serializers.ValidationError(
                {"encounter": "The selected encounter does not belong to this patient."}
            )

        if encounter:
            duplicate_qs = StructuredReport.objects.filter(encounter=encounter)
            if self.instance:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                raise serializers.ValidationError(
                    {"encounter": "A structured report already exists for this encounter. Edit the existing report instead."}
                )

        return attrs

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


    def get_signed_by_display(self, obj):
        user = obj.signed_by
        if not user:
            return ""
        return (
            user.get_full_name()
            or getattr(user, "username", "")
            or getattr(user, "email", "")
            or str(user)
        )

    def get_issued_by_display(self, obj):
        user = obj.issued_by
        if not user:
            return ""
        return (
            user.get_full_name()
            or getattr(user, "username", "")
            or getattr(user, "email", "")
            or str(user)
        )


class PatientReportDeliverySerializer(serializers.ModelSerializer):
    report_id_display = serializers.CharField(
        source="report.report_id",
        read_only=True,
    )
    patient_name = serializers.SerializerMethodField()
    requested_by_display = serializers.SerializerMethodField()
    sent_by_display = serializers.SerializerMethodField()

    class Meta:
        model = PatientReportDelivery
        fields = [
            "id",
            "report",
            "report_id_display",
            "patient",
            "patient_name",
            "channel",
            "recipient",
            "include_images",
            "consent_confirmed",
            "requested_by",
            "requested_by_display",
            "sent_by",
            "sent_by_display",
            "status",
            "failure_reason",
            "sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "patient",
            "requested_by",
            "sent_by",
            "status",
            "failure_reason",
            "sent_at",
        ]

    def get_patient_name(self, obj):
        return (
            f"{obj.patient.first_name} "
            f"{obj.patient.last_name}"
        ).strip()

    def get_requested_by_display(self, obj):
        user = obj.requested_by
        return (
            user.get_full_name() or user.username or user.email
            if user
            else ""
        )

    def get_sent_by_display(self, obj):
        user = obj.sent_by
        return (
            user.get_full_name() or user.username or user.email
            if user
            else ""
        )
