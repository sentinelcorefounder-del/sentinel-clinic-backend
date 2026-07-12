from rest_framework import serializers
from .models import ScreeningEncounter


class ScreeningEncounterSerializer(serializers.ModelSerializer):
    poor_va_flag = serializers.SerializerMethodField()
    poor_va_reason = serializers.SerializerMethodField()
    source_hospital_name = serializers.SerializerMethodField()
    originating_organization_name = serializers.SerializerMethodField()

    class Meta:
        model = ScreeningEncounter

        fields = [
            "id",
            "encounter_id",
            "patient",
            "encounter_date",
            "encounter_type",
            "programme",
            "source_type",
            "workflow_route",
            "payment_responsibility",
            "originating_organization",
            "originating_organization_name",
            "hospital_referral",
            "source_hospital_name",
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
            "poor_va_flag",
            "poor_va_reason",
            "created_at",
            "updated_at",
        ]

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
