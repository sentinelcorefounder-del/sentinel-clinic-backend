from rest_framework import serializers


class HospitalReferralStatusSyncSerializer(serializers.Serializer):
    referral_id = serializers.CharField(max_length=60)

    matched_clinic_code = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
    )
    matched_clinic_name = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    linked_patient_id = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
    )

    referral_status = serializers.ChoiceField(
        choices=["submitted", "clinic_matched", "completed", "cancelled"],
        required=False,
    )

    report_ready = serializers.BooleanField(required=False)
    report_id = serializers.CharField(
        max_length=30,
        required=False,
        allow_blank=True,
    )

    payout_status = serializers.ChoiceField(
        choices=["not_due", "pending", "approved", "paid"],
        required=False,
    )

    notes = serializers.CharField(required=False, allow_blank=True)