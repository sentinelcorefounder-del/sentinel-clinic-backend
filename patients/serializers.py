from rest_framework import serializers
from .models import Patient


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = [
            "id",
            "patient_id",
            "first_name",
            "last_name",
            "date_of_birth",
            "sex",
            "phone",
            "email",
            "address",
            "city",
            "state",
            "country",
            "consent_status",
            "created_at",
            "updated_at",
        ]