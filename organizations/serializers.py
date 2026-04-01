from rest_framework import serializers


class OrganizationSyncSerializer(serializers.Serializer):
    clinic_id = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=255)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)