from rest_framework import serializers
from .models import ConsentRecord


class ConsentRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentRecord
        fields = [
            "id",
            "consent_id",
            "patient",
            "encounter",
            "consent_type",
            "consent_status",
            "consent_date",
            "captured_by",
            "expiry_date",
            "withdrawal_date",
            "notes",
            "created_at",
            "updated_at",
        ]