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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        path = f"/api/encounters/ocular-investigations/{instance.pk}/content/"
        request = self.context.get("request")
        data["file"] = request.build_absolute_uri(path) if request else path
        return data

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
            "report_layout", "selected_fundus_upload_ids",
            "selected_ocular_investigation_ids", "attachment_captions",
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

    def validate(self, attrs):
        assessment = self.instance
        if not assessment:
            return attrs
        layout = attrs.get("report_layout", assessment.report_layout)
        fundus_ids = attrs.get(
            "selected_fundus_upload_ids",
            assessment.selected_fundus_upload_ids,
        )
        investigation_ids = attrs.get(
            "selected_ocular_investigation_ids",
            assessment.selected_ocular_investigation_ids,
        )
        if layout == "text_only" and (fundus_ids or investigation_ids):
            raise serializers.ValidationError({
                "report_layout": "Text-only reports cannot include attachments."
            })
        valid_fundus_ids = set(
            assessment.encounter.image_uploads.filter(
                id__in=fundus_ids
            ).values_list("id", flat=True)
        )
        if valid_fundus_ids != set(fundus_ids):
            raise serializers.ValidationError({
                "selected_fundus_upload_ids": (
                    "Every selected fundus image must belong to this encounter."
                )
            })
        valid_investigation_ids = set(
            assessment.encounter.ocular_investigations.filter(
                id__in=investigation_ids
            ).values_list("id", flat=True)
        )
        if valid_investigation_ids != set(investigation_ids):
            raise serializers.ValidationError({
                "selected_ocular_investigation_ids": (
                    "Every selected investigation must belong to this encounter."
                )
            })
        return attrs


class ScreeningEncounterSerializer(serializers.ModelSerializer):
    assessment_location_type = serializers.ChoiceField(
        choices=[("clinic", "Clinic"), ("hospital", "Hospital"), ("mobile", "Mobile / client site")],
        write_only=True, required=False,
    )
    assessment_location_name = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=255)
    assessment_location_address = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=500)
    poor_va_flag = serializers.SerializerMethodField()
    poor_va_reason = serializers.SerializerMethodField()
    source_hospital_name = serializers.SerializerMethodField()
    originating_organization_name = serializers.SerializerMethodField()
    ocular_assessment = OcularDiagnosticAssessmentSerializer(read_only=True)
    service_package_locked = serializers.SerializerMethodField()
    targeted_screening_report_status = serializers.SerializerMethodField()

    class Meta:
        model = ScreeningEncounter

        fields = [
            "id",
            "encounter_id",
            "patient",
            "encounter_date",
            "encounter_type",
            "programme",
            "service_package",
            "service_package_locked",
            "targeted_screening_report_status",
            "assessment_location_snapshot",
            "assessment_location_type",
            "assessment_location_name",
            "assessment_location_address",
            "ocular_assessment",
            "source_type",
            "workflow_route",
            "payment_responsibility",
            "originating_organization",
            "originating_organization_name",
            "service_branch",
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
            "service_branch",
            "assessment_location_snapshot",
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
                "service_package",
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

    def create(self, validated_data):
        location_type = validated_data.pop("assessment_location_type", "")
        location_name = (validated_data.pop("assessment_location_name", "") or "").strip()
        location_address = (validated_data.pop("assessment_location_address", "") or "").strip()
        branch = validated_data.get("service_branch")
        if not location_type and branch:
            location_type = "clinic"
        if not location_name and branch:
            location_name = branch.name
            location_address = location_address or branch.address
        if not location_type or not location_name:
            raise serializers.ValidationError({
                "assessment_location_name": "Location type and site/location name are required."
            })
        validated_data["assessment_location_snapshot"] = {
            "location_type": location_type,
            "site_name": location_name,
            "address": location_address,
            "branch_id": branch.pk if branch else None,
            "branch_code": branch.branch_code if branch else "",
            "branch_name": branch.name if branch else "",
        }
        return super().create(validated_data)

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

    def get_service_package_locked(self, obj):
        try:
            if obj.structured_report.report_status not in {
                "draft", "under_review", "returned_to_clinic", "ops_rejected"
            }:
                return True
        except Exception:
            pass
        try:
            eye_report = obj.eye_health_report
            eye_correction_open = bool(
                eye_report.status == "draft" and eye_report.correction_source_version_id
            )
            if eye_report.versions.exists() and not eye_correction_open:
                return True
        except Exception:
            pass
        try:
            return bool(obj.ocular_assessment.completed_at)
        except Exception:
            return False

    def get_targeted_screening_report_status(self, obj):
        try:
            report = obj.eye_health_report
        except Exception:
            return "not_started"
        if report.status == "finalized":
            return "finalized"
        if report.previewed_at:
            return "previewed"
        return "draft"
