from rest_framework import serializers


class HospitalReferralSubmitSerializer(serializers.Serializer):
    DIABETES_TYPE_CHOICES = [
        ("type_1", "Type 1"),
        ("type_2", "Type 2"),
        ("other", "Other"),
        ("unknown", "Unknown"),
    ]

    PATIENT_SEX_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
        ("prefer_not_to_say", "Prefer not to say"),
    ]

    patient_id = serializers.CharField(max_length=50)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    dob = serializers.DateField()
    patient_sex = serializers.ChoiceField(choices=PATIENT_SEX_CHOICES)
    hospital_mrn = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )
    diabetes_type = serializers.ChoiceField(choices=DIABETES_TYPE_CHOICES)
    reason_for_referral = serializers.CharField()
    phone_number = serializers.CharField(
        max_length=30,
        required=False,
        allow_blank=True,
    )
    email = serializers.EmailField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)