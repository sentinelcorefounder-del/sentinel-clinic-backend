from rest_framework import serializers
from .models import (
    OcularAIReview,
    OcularDiagnosticAssessment,
    OcularInvestigation,
    ScreeningEncounter,
)


class OcularInvestigationSerializer(serializers.ModelSerializer):
    uploaded_by_display = serializers.SerializerMethodField()

    class Meta:
        model = OcularInvestigation
        fields = [
            "id", "investigation_id", "encounter", "investigation_type",
            "laterality", "test_type", "device_name", "performed_at",
            "reliability", "reliability_notes", "interpretation", "file",
            "original_filename", "uploaded_by_display", "uploaded_at",
        ]
        read_only_fields = [
            "investigation_id", "encounter", "original_filename",
            "uploaded_by_display", "uploaded_at",
        ]

    def get_uploaded_by_display(self, obj):
        user = obj.uploaded_by
        return (user.get_full_name() or user.username) if user else ""

    def validate_file(self, value):
        if value.size > 25 * 1024 * 1024:
            raise serializers.ValidationError("Maximum file size is 25 MB.")
        allowed = {"application/pdf", "image/jpeg", "image/png"}
        if getattr(value, "content_type", "") not in allowed:
            raise serializers.ValidationError("Upload a PDF, JPEG, or PNG file.")
        return value


class OcularAIReviewSerializer(serializers.ModelSerializer):
    requested_by_display = serializers.SerializerMethodField()
    decided_by_display = serializers.SerializerMethodField()
    encounter_changed_since_review = serializers.SerializerMethodField()

    class Meta:
        model = OcularAIReview
        fields = [
            "id", "review_id", "encounter", "status", "provider",
            "fee_amount", "fee_currency", "payment_status", "pricing_rule",
            "model_version", "clinician_impression_snapshot",
            "clinician_management_snapshot", "suspected_conditions",
            "supporting_findings", "differential_diagnoses",
            "suggested_urgency", "suggested_management", "limitations",
            "agreement_status", "disagreement_reasons",
            "expert_review_required", "error_message", "clinician_decision",
            "clinician_decision_notes", "requested_by_display",
            "decided_by_display", "decided_at", "requested_at", "completed_at",
            "encounter_changed_since_review",
            "clinical_ai_consent", "training_consent", "consent_checked_at",
            "privacy_verified_at", "deidentified_review_reference",
            "transmitted_data_manifest",
        ]
        read_only_fields = fields

    def get_requested_by_display(self, obj):
        user = obj.requested_by
        return (user.get_full_name() or user.username) if user else ""

    def get_decided_by_display(self, obj):
        user = obj.decided_by
        return (user.get_full_name() or user.username) if user else ""

    def get_encounter_changed_since_review(self, obj):
        try:
            assessment = obj.encounter.ocular_assessment
        except OcularDiagnosticAssessment.DoesNotExist:
            return True
        return (
            assessment.impression != obj.clinician_impression_snapshot
            or assessment.management_plan != obj.clinician_management_snapshot
        )


class OcularDiagnosticAssessmentSerializer(serializers.ModelSerializer):
    completed_by_display = serializers.SerializerMethodField()

    class Meta:
        model = OcularDiagnosticAssessment
        fields = [
            "id", "encounter", "fundus_photography_performed",
            "visual_field_performed", "tonometry_performed",
            "visual_acuity_performed", "anterior_eye_assessment_performed",
            "presenting_complaint", "ocular_history", "anterior_eye_findings",
            "fundus_findings", "visual_field_summary", "tonometry_summary",
            "impression", "management_plan", "management_outcome",
            "completed_at", "completed_by", "completed_by_display",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "encounter", "completed_at", "completed_by",
            "completed_by_display", "created_at", "updated_at",
        ]

    def get_completed_by_display(self, obj):
        user = obj.completed_by
        return user.get_full_name() or user.username if user else ""


class ScreeningEncounterSerializer(serializers.ModelSerializer):
    poor_va_flag = serializers.SerializerMethodField()
    poor_va_reason = serializers.SerializerMethodField()
    source_hospital_name = serializers.SerializerMethodField()
    originating_organization_name = serializers.SerializerMethodField()
    ocular_assessment = OcularDiagnosticAssessmentSerializer(read_only=True)

    class Meta:
        model = ScreeningEncounter

        fields = [
            "id",
            "encounter_id",
            "patient",
            "encounter_date",
            "encounter_type",
            "programme",
            "ocular_assessment",
            "source_type",
            "workflow_route",
            "payment_responsibility",
            "originating_organization",
            "originating_organization_name",
            "hospital_referral",
            "source_hospital_name",
            "source_override_reason",
            "source_overridden_by",
            "source_overridden_at",
            "screening_status",

            # Legacy fields
            "visual_acuity_left",
            "visual_acuity_right",

            # Technician VA capture
            "left_unaided_va",
            "right_unaided_va",
            "left_corrected_pinhole_va",
            "right_corrected_pinhole_va",
            "left_va_method",
            "right_va_method",

            # technician/clinical intake
            "diabetes_duration",
            "symptoms_notes",
            "clinical_notes",

            # IOP
            "iop_before_dilation_left",
            "iop_before_dilation_right",
            "iop_after_dilation_left",
            "iop_after_dilation_right",

            "dilation_drops_used",
            "dilation_notes",

            # system flag
            "poor_va_flag",
            "poor_va_reason",

            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "screening_status",
            "originating_organization",
            "originating_organization_name",
            "source_hospital_name",
            "source_overridden_by",
            "source_overridden_at",
            "poor_va_flag",
            "poor_va_reason",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)

        if instance is not None:
            immutable_fields = {
                "patient",
                "source_type",
                "hospital_referral",
                "originating_organization",
                "workflow_route",
                "payment_responsibility",
                "programme",
            }
            changed = []

            for field in immutable_fields:
                if field not in attrs:
                    continue

                old_value = getattr(instance, field)
                new_value = attrs[field]
                old_pk = getattr(old_value, "pk", old_value)
                new_pk = getattr(new_value, "pk", new_value)

                if old_pk != new_pk:
                    changed.append(field)

            if changed:
                raise serializers.ValidationError({
                    "detail": (
                        "Encounter source and routing are fixed when the "
                        "assessment is created. Create a new assessment "
                        "episode instead of changing: "
                        + ", ".join(sorted(changed))
                        + "."
                    )
                })

        return attrs

    def _normalise_va(self, value):
        return (value or "").strip().lower().replace(" ", "")

    def _is_poor_va(self, value):
        poor_values = {
            "6/12",
            "6/15",
            "6/18",
            "6/24",
            "6/36",
            "6/60",
            "cf",
            "countingfingers",
            "hm",
            "handmovements",
            "pl",
            "npl",
            "nlp",
        }

        return self._normalise_va(value) in poor_values

    def get_poor_va_flag(self, obj):
        return (
            self._is_poor_va(obj.left_corrected_pinhole_va)
            or self._is_poor_va(obj.right_corrected_pinhole_va)
        )

    def get_poor_va_reason(self, obj):
        reasons = []

        if self._is_poor_va(obj.left_corrected_pinhole_va):
            method = obj.left_va_method or "corrected/pinhole"
            reasons.append(f"Left eye {method} VA is {obj.left_corrected_pinhole_va}")

        if self._is_poor_va(obj.right_corrected_pinhole_va):
            method = obj.right_va_method or "corrected/pinhole"
            reasons.append(f"Right eye {method} VA is {obj.right_corrected_pinhole_va}")

        return "; ".join(reasons)

    def get_source_hospital_name(self, obj):
        referral = getattr(obj, "hospital_referral", None)
        hospital = getattr(referral, "source_hospital", None) if referral else None
        return hospital.name if hospital else ""

    def get_originating_organization_name(self, obj):
        organization = getattr(obj, "originating_organization", None)
        return organization.name if organization else ""
