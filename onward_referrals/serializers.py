from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from common.tenant import get_user_organization

from .models import (
    EncounterResponsibleOptometrist,
    OnwardReferral,
    OnwardReferralAvailability,
    OnwardReferralEvent,
    OnwardReferralVersion,
)
from .services import source_is_stale
from .permissions import can_distribute, clinical_capabilities


class ResponsibilitySerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="optometrist.username", read_only=True)

    class Meta:
        model = EncounterResponsibleOptometrist
        fields = [
            "username", "clinician_name", "professional_role",
            "registration_number", "accepted_at", "takeover_reason",
        ]


class OnwardReferralVersionSerializer(serializers.ModelSerializer):
    stale_source_warning = serializers.SerializerMethodField()
    document_path = serializers.SerializerMethodField()
    recipient_name = serializers.CharField(source="recipient_organization.name", read_only=True)

    class Meta:
        model = OnwardReferralVersion
        fields = [
            "version_number", "status", "urgency", "referral_reason",
            "requested_specialist_action", "relevant_history",
            "pertinent_findings", "professional_impression",
            "management_provided", "include_patient_phone",
            "recipient_organization", "recipient_name", "recipient_department",
            "emergency_escalation_confirmed", "emergency_escalation_method",
            "emergency_escalation_note", "finalized_at", "amendment_reason",
            "void_reason", "stale_source_warning", "document_path",
        ]
        read_only_fields = ["version_number", "status", "finalized_at", "amendment_reason", "void_reason"]

    def get_stale_source_warning(self, obj):
        return bool(obj.status in {"finalized", "superseded"} and source_is_stale(obj))

    def get_document_path(self, obj):
        if obj.status not in {"finalized", "superseded"}:
            return ""
        return f"/api/onward-referrals/{obj.referral.referral_uuid}/versions/{obj.version_number}/document/"


class AvailabilitySerializer(serializers.ModelSerializer):
    recipient_name = serializers.CharField(source="recipient_organization.name", read_only=True)

    class Meta:
        model = OnwardReferralAvailability
        fields = ["state", "recipient_name", "granted_at"]


class EventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = OnwardReferralEvent
        fields = ["event_type", "safe_note", "actor_name", "created_at"]

    def get_actor_name(self, obj):
        return obj.actor.get_full_name() or obj.actor.username


class OnwardReferralSerializer(serializers.ModelSerializer):
    current_version = OnwardReferralVersionSerializer(read_only=True)
    versions = OnwardReferralVersionSerializer(many=True, read_only=True)
    events = EventSerializer(many=True, read_only=True)
    responsibility = serializers.SerializerMethodField()
    patient_name = serializers.SerializerMethodField()
    encounter_reference = serializers.CharField(source="encounter.encounter_id", read_only=True)
    clinic_name = serializers.CharField(source="originating_clinic.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    inbound_referral_reference = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = OnwardReferral
        fields = [
            "referral_uuid", "referral_reference", "patient_name",
            "encounter_reference", "clinic_name", "branch_name",
            "inbound_referral_reference", "clinical_sources", "route",
            "lifecycle", "responsibility", "current_version", "versions",
            "events", "capabilities", "created_at", "updated_at",
        ]

    def get_responsibility(self, obj):
        value = getattr(obj.encounter, "onward_responsibility", None)
        return ResponsibilitySerializer(value).data if value else None

    def get_patient_name(self, obj):
        return f"{obj.patient.first_name} {obj.patient.last_name}".strip()

    def get_inbound_referral_reference(self, obj):
        if obj.route != OnwardReferral.Route.ORIGINATING_HOSPITAL:
            return ""
        inbound = obj.original_hospital_referral
        return inbound.referral_id if inbound else ""

    def get_capabilities(self, obj):
        request = self.context.get("request")
        if not request:
            return {
                "can_accept_responsibility": False, "can_author": False,
                "can_administer_recipient": False, "can_distribute": False,
            }
        responsibility = getattr(obj.encounter, "onward_responsibility", None)
        values = clinical_capabilities(
            request.user, clinic=obj.originating_clinic, branch=obj.branch,
            responsibility=responsibility,
        )
        try:
            values["can_distribute"] = can_distribute(request.user, obj)
        except PermissionDenied:
            values["can_distribute"] = False
        return values

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        organization = get_user_organization(request.user) if request else None
        if organization and organization.organization_type == "hospital":
            data.pop("events", None)
            data.pop("responsibility", None)
            data["capabilities"] = {
                "can_accept_responsibility": False, "can_author": False,
                "can_administer_recipient": False, "can_distribute": False,
            }
            available_versions = instance.versions.filter(
                availabilities__recipient_organization=organization,
                availabilities__state__in={"active", "superseded"},
            ).distinct().order_by("version_number")
            data["versions"] = OnwardReferralVersionSerializer(
                available_versions, many=True, context=self.context,
            ).data
            data["current_version"] = data["versions"][-1] if data["versions"] else None
            if data["current_version"]:
                data["lifecycle"] = data["current_version"]["status"]
        return data
