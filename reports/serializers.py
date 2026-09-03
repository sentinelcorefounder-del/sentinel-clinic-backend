from rest_framework import serializers
from .models import (
    PatientReportDelivery,
    ReportStatusEvent,
    StructuredReport,
    StructuredReportVersion,
    EyeHealthScreeningReport,
    EyeHealthScreeningReportVersion,
)


class EyeHealthScreeningReportVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EyeHealthScreeningReportVersion
        fields = [
            "id", "version_number", "checksum_sha256", "clinician_snapshot",
            "attachment_manifest", "pdf_checksum_sha256", "pdf_size", "created_at",
            "purpose", "correction_note", "source_version",
        ]
        read_only_fields = fields


class EyeHealthScreeningReportSerializer(serializers.ModelSerializer):
    finalized_version_detail = EyeHealthScreeningReportVersionSerializer(
        source="finalized_version", read_only=True
    )
    professional_defaults = serializers.SerializerMethodField()
    clean_pdf_ready = serializers.SerializerMethodField()

    class Meta:
        model = EyeHealthScreeningReport
        fields = [
            "id", "encounter", "outcome", "selected_advice", "advice",
            "structured_findings", "generated_suggestion", "clinical_summary",
            "right_visual_field_result", "left_visual_field_result",
            "right_fundus_result", "left_fundus_result",
            "selected_fundus_upload_ids", "selected_visual_field_investigation_ids",
            "status", "previewed_at", "lock_version", "finalized_version",
            "finalized_version_detail", "professional_defaults", "created_at", "updated_at",
            "correction_source_version",
            "hospital_released_version", "hospital_released_at",
            "clean_pdf_ready",
        ]
        read_only_fields = [
            "encounter", "status", "previewed_at", "lock_version", "finalized_version",
            "finalized_version_detail", "professional_defaults", "created_at", "updated_at",
            "correction_source_version", "generated_suggestion",
            "hospital_released_version", "hospital_released_at",
            "clean_pdf_ready",
        ]

    def validate_structured_findings(self, value):
        from .eye_health import normalise_structured_findings
        try:
            return normalise_structured_findings(value)
        except Exception as exc:
            raise serializers.ValidationError(
                exc.messages if hasattr(exc, "messages") else str(exc)
            ) from exc

    def get_professional_defaults(self, obj):
        user = self.context.get("request").user if self.context.get("request") else None
        profile = getattr(user, "clinical_professional_profile", None)
        if not profile or not profile.is_verified:
            return None
        return {
            "display_name": profile.display_name,
            "professional_role": profile.professional_role,
            "registration_number": profile.registration_number,
            "qualifications": profile.qualifications,
        }

    def get_clean_pdf_ready(self, obj):
        from .distribution import targeted_clean_pdf_ready
        return targeted_clean_pdf_ready(obj)


class StructuredReportVersionSerializer(serializers.ModelSerializer):
    editor_display = serializers.SerializerMethodField()

    class Meta:
        model = StructuredReportVersion
        fields = [
            "id", "version_number", "checksum_sha256", "editor_display",
            "responsibility_snapshot", "purpose", "correction_note",
            "source_version", "pdf_checksum_sha256", "pdf_size",
            "pdf_generated_at", "legacy_pdf_unbound", "created_at",
        ]

    def get_editor_display(self, obj):
        if not obj.editor:
            return "Unknown historical author"
        return obj.editor.get_full_name() or obj.editor.username or obj.editor.email


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
            "source_version",
            "target_version",
            "authority_used",
            "correction_note",
            "created_at",
        ]

    def get_actor_display(self, obj):
        if not obj.actor:
            return "System"
        return obj.actor.get_full_name() or obj.actor.username or obj.actor.email


class StructuredReportSerializer(serializers.ModelSerializer):
    expected_version = serializers.IntegerField(write_only=True, required=False)
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
    versions = StructuredReportVersionSerializer(many=True, read_only=True)
    clinical_responsibility = serializers.SerializerMethodField()

    class Meta:
        model = StructuredReport
        extra_kwargs = {"encounter": {"validators": []}}
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
            "report_layout",
            "selected_fundus_upload_ids",
            "selected_ocular_investigation_ids",
            "attachment_captions",
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
            "lock_version",
            "expected_version",
            "submitted_version",
            "issued_version",
            "clinical_responsibility",
            "versions",
            "status_events",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "report_id",
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
            "lock_version",
            "submitted_version",
            "issued_version",
            "clinical_responsibility",
            "versions",
            "status_events",
            "created_at",
            "updated_at",
        ]

    def get_clinical_responsibility(self, obj):
        responsibility = getattr(obj, "clinical_responsibility", None)
        if not responsibility:
            return None
        return {
            "current_clinician": responsibility.current_clinician_id,
            "original_clinician": responsibility.original_clinician_id,
            "clinician_name": responsibility.clinician_name,
            "professional_role": responsibility.professional_role,
            "registration_number": responsibility.registration_number,
            "authority_used": responsibility.authority_used,
            "clinic": responsibility.clinic_id,
            "clinic_name": responsibility.clinic.name,
            "branch": responsibility.branch_id,
            "branch_name": responsibility.branch.name,
            "accepted_at": responsibility.accepted_at,
            "takeover_reason": responsibility.takeover_reason,
        }


    def get_patient_name(self, obj):
        patient = obj.patient
        return f"{patient.first_name} {patient.last_name}".strip() if patient else ""

    def get_sentinel_patient_id(self, obj):
        patient = obj.patient
        master_patient = getattr(patient, "master_patient", None) if patient else None
        return getattr(master_patient, "sentinel_patient_id", "") or getattr(patient, "sentinel_patient_id", "") or ""

    def validate(self, attrs):
        attrs.pop("expected_version", None)
        encounter = attrs.get("encounter") or getattr(self.instance, "encounter", None)
        patient = attrs.get("patient") or getattr(self.instance, "patient", None)

        if encounter and patient and encounter.patient_id != patient.id:
            raise serializers.ValidationError(
                {"encounter": "The selected encounter does not belong to this patient."}
            )

        if encounter and self.instance is None and not encounter.includes_diabetic_screening:
            raise serializers.ValidationError({
                "encounter": "Diabetic grading is available only for diabetic or combined service packages."
            })

        if self.instance:
            immutable = {
                "report_id": self.instance.report_id,
                "encounter": self.instance.encounter_id,
                "patient": self.instance.patient_id,
            }
            for field, original in immutable.items():
                if field in attrs:
                    incoming = getattr(attrs[field], "pk", attrs[field])
                    if incoming != original:
                        raise serializers.ValidationError(
                            {field: "Report identity and ownership cannot change after creation."}
                        )

        if encounter and self.instance:
            duplicate_qs = StructuredReport.objects.filter(encounter=encounter)
            duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                raise serializers.ValidationError(
                    {"encounter": "A structured report already exists for this encounter. Edit the existing report instead."}
                )

        layout = attrs.get("report_layout", getattr(self.instance, "report_layout", "text_only"))
        fundus_ids = attrs.get(
            "selected_fundus_upload_ids",
            getattr(self.instance, "selected_fundus_upload_ids", []),
        )
        investigation_ids = attrs.get(
            "selected_ocular_investigation_ids",
            getattr(self.instance, "selected_ocular_investigation_ids", []),
        )
        if layout == "text_only" and (fundus_ids or investigation_ids):
            raise serializers.ValidationError(
                {"report_layout": "Text-only reports cannot include investigation attachments."}
            )
        if encounter:
            valid_upload_ids = set(
                encounter.image_uploads.filter(id__in=fundus_ids).values_list("id", flat=True)
            )
            if valid_upload_ids != set(fundus_ids):
                raise serializers.ValidationError(
                    {"selected_fundus_upload_ids": "Every selected fundus image must belong to this encounter."}
                )
            valid_investigation_ids = set(
                encounter.ocular_investigations.filter(id__in=investigation_ids).values_list("id", flat=True)
            )
            if valid_investigation_ids != set(investigation_ids):
                raise serializers.ValidationError(
                    {"selected_ocular_investigation_ids": "Every selected investigation must belong to this encounter."}
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
