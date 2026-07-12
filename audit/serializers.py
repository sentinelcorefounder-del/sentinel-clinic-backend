from rest_framework import serializers

from .models import PatientTimelineEvent


class PatientTimelineEventSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()
    organization_display = serializers.SerializerMethodField()

    class Meta:
        model = PatientTimelineEvent
        fields = [
            "id",
            "category",
            "event_type",
            "title",
            "description",
            "source_type",
            "source_id",
            "encounter_id",
            "report_id",
            "referral_id",
            "payment_id",
            "actor_display",
            "organization_display",
            "visibility",
            "metadata",
            "occurred_at",
        ]

    def get_actor_display(self, obj):
        if not obj.actor:
            return "System"
        return obj.actor.get_full_name() or obj.actor.username or obj.actor.email

    def get_organization_display(self, obj):
        return obj.organization.name if obj.organization else ""
