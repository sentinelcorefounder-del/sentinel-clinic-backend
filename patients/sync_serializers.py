from rest_framework import serializers


class PatientSyncSerializer(serializers.Serializer):
    patient_id = serializers.CharField(max_length=30)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    date_of_birth = serializers.DateField()
    sex = serializers.ChoiceField(choices=["male", "female", "other"])
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    state = serializers.CharField(max_length=100, required=False, allow_blank=True)
    country = serializers.CharField(max_length=100, required=False, allow_blank=True)
    consent_status = serializers.CharField(max_length=30, required=False, allow_blank=True)

    assigned_clinic_id = serializers.CharField(max_length=50)
    referral_id = serializers.CharField(max_length=50, required=False, allow_blank=True)
    referral_status = serializers.CharField(max_length=50, required=False, allow_blank=True)
    appointment_date = serializers.DateField(required=False, allow_null=True)