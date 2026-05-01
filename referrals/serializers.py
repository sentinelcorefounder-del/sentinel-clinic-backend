from rest_framework import serializers
from .models import HospitalReferral


class HospitalReferralSerializer(serializers.ModelSerializer):
    source_hospital_name = serializers.CharField(source="source_hospital.name", read_only=True)
    matched_clinic_name = serializers.CharField(source="matched_clinic.name", read_only=True)
    report_id_display = serializers.CharField(source="report.report_id", read_only=True)
    report_pk = serializers.IntegerField(source="report.id", read_only=True)

    patient_linked_id = serializers.CharField(source="patient.patient_id", read_only=True)

    report_pdf_url = serializers.SerializerMethodField()

    class Meta:
        model = HospitalReferral
        fields = [
            "id",
            "referral_id",
            "source_hospital",
            "source_hospital_name",
            "patient",
            "patient_linked_id",
            "patient_id_text",
            "first_name",
            "last_name",
            "dob",
            "patient_sex",
            "hospital_mrn",
            "diabetes_type",
            "reason_for_referral",
            "phone_number",
            "email",
            "matched_clinic",
            "matched_clinic_name",
            "report",
            "report_pk",
            "report_id_display",
            "report_pdf_url",
            "referral_date",
            "referral_status",
            "report_ready",
            "hospital_commission_amount",
            "payout_status",
            "payout_date",
            "baserow_row_id",
            "source_system",
            "notes",
            "submitted_by_username",
            "created_at",
            "updated_at",
        ]

    def get_report_pdf_url(self, obj):
        if not obj.report_id:
            return ""

        request = self.context.get("request")
        path = f"/api/reports/{obj.report_id}/pdf/"

        if request:
            return request.build_absolute_uri(path)

        return path
