from rest_framework import serializers
from .models import Organization


class OrganizationSyncSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = [
            "clinic_id",
            "name",
            "contact_email",
            "is_active",
        ]