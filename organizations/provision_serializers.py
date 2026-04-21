from rest_framework import serializers


class ClinicProvisionSerializer(serializers.Serializer):
    clinic_id = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=255)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    report_signatory_name = serializers.CharField(required=False, allow_blank=True)
    report_signatory_title = serializers.CharField(required=False, allow_blank=True)
    report_signatory_odorbn = serializers.CharField(required=False, allow_blank=True)
    report_footer_note = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False, default=True)

    admin_username = serializers.CharField(max_length=150)
    admin_email = serializers.EmailField(required=False, allow_blank=True)
    admin_first_name = serializers.CharField(required=False, allow_blank=True)
    admin_last_name = serializers.CharField(required=False, allow_blank=True)
    admin_role = serializers.CharField(required=False, default="clinic_admin")
    temporary_password = serializers.CharField(required=False, allow_blank=True)


class HospitalProvisionSerializer(serializers.Serializer):
    hospital_id = serializers.CharField(max_length=50)
    hospital_name = serializers.CharField(max_length=255)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False, default=True)

    admin_username = serializers.CharField(max_length=150)
    admin_email = serializers.EmailField(required=False, allow_blank=True)
    admin_first_name = serializers.CharField(required=False, allow_blank=True)
    admin_last_name = serializers.CharField(required=False, allow_blank=True)
    admin_role = serializers.CharField(required=False, default="hospital_admin")
    temporary_password = serializers.CharField(required=False, allow_blank=True)