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

class ClinicDirectPatientCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = [
            "first_name", "last_name", "date_of_birth", "sex",
            "phone", "email", "address", "city", "state", "country",
        ]

    def validate(self, attrs):
        duplicate = Patient.objects.filter(
            first_name__iexact=(attrs.get("first_name") or "").strip(),
            last_name__iexact=(attrs.get("last_name") or "").strip(),
            date_of_birth=attrs.get("date_of_birth"),
        ).first()
        if duplicate:
            raise serializers.ValidationError({
                "non_field_errors": [
                    "A patient with the same name and date of birth already exists. Search for the existing patient first."
                ]
            })
        return attrs
