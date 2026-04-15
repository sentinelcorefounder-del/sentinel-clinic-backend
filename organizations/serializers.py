from rest_framework import serializers
from .models import Organization


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = [
            "id",
            "clinic_id",
            "name",
            "address",
            "contact_email",
            "phone",
            "logo",
            "report_signatory_name",
            "report_signatory_title",
            "report_signatory_odorbn",
            "created_at",
            "updated_at",
        ]