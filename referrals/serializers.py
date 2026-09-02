from rest_framework import serializers
from .models import HospitalReferral
from reports.release_control import (
    hospital_visible_report_status,
    is_report_released_to_hospital,
)


class HospitalReferralSerializer(serializers.ModelSerializer):
    source_hospital_name = serializers.CharField(source="source_hospital.name", read_only=True)
    matched_clinic_name = serializers.CharField(source="matched_clinic.name", read_only=True)
    source_branch_name = serializers.CharField(source="source_branch.name", read_only=True)
    matched_branch_name = serializers.CharField(source="matched_branch.name", read_only=True)
    report_id_display = serializers.CharField(source="report.report_id", read_only=True)
    report_pk = serializers.IntegerField(source="report.id", read_only=True)
    patient_linked_id = serializers.CharField(source="patient.patient_id", read_only=True)
    report_pdf_url = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    payment_amount = serializers.SerializerMethodField()
    payment_currency = serializers.SerializerMethodField()
    payment_reference = serializers.SerializerMethodField()
    payment_paid_at = serializers.SerializerMethodField()
    report_status = serializers.SerializerMethodField()
    report_issued_at = serializers.SerializerMethodField()
    targeted_patient_report_url = serializers.SerializerMethodField()
    targeted_clinician_report_url = serializers.SerializerMethodField()
    combined_patient_bundle_url = serializers.SerializerMethodField()
    combined_clinician_bundle_url = serializers.SerializerMethodField()

    class Meta:
        model = HospitalReferral
        fields = [
            "id",
            "referral_id",
            "source_hospital",
            "source_hospital_name",
            "source_branch", "source_branch_name",
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
            "matched_branch", "matched_branch_name",
            "report",
            "report_pk",
            "report_id_display",
            "report_pdf_url",
            "referral_date",
            "referral_status",
            "report_ready",
            "report_status",
            "report_issued_at",
            "targeted_patient_report_url", "targeted_clinician_report_url",
            "combined_patient_bundle_url", "combined_clinician_bundle_url",
            "hospital_commission_amount",
            "payout_status",
            "payout_date",
            "payment_status",
            "payment_amount",
            "payment_currency",
            "payment_reference",
            "payment_paid_at",
            "source_system",
            "notes",
            "submitted_by_username",
            "created_at",
            "updated_at",
        ]

    def get_report_status(self, obj):
        return hospital_visible_report_status(obj.report, obj)

    def get_report_issued_at(self, obj):
        if not obj.report_id or not obj.report.issued_at:
            return ""
        return obj.report.issued_at

    def to_representation(self, instance):
        data = super().to_representation(instance)
        report = instance.report
        targeted = self._released_targeted_report(instance)

        if not report:
            data["report_ready"] = bool(targeted)
            if targeted:
                data["referral_status"] = "report_issued"
            return data

        status_map = {
            "under_review": "report_created",
            "signed_off": "report_created",
            "submitted_to_ops": "submitted_to_ops",
            "returned_to_clinic": "returned_to_clinic",
            "ops_rejected": "returned_to_clinic",
            "ops_approved": "submitted_to_ops",
            "issued": "submitted_to_ops",
        }

        released = is_report_released_to_hospital(report, instance)

        if not released:
            data["report_ready"] = False
            data["report_pdf_url"] = ""
            data["referral_status"] = status_map.get(
                report.report_status,
                data.get("referral_status", "in_clinic"),
            )
        else:
            data["report_ready"] = instance.report_ready
            if instance.referral_status != "completed":
                data["referral_status"] = "report_issued"

        return data

    def get_report_pdf_url(self, obj):
        if not is_report_released_to_hospital(obj.report, obj):
            return ""

        request = self.context.get("request")
        path = f"/api/reports/{obj.report_id}/pdf/"

        if request:
            return request.build_absolute_uri(path)

        return path

    def _released_targeted_report(self, obj):
        encounter = obj.screening_encounters.select_related(
            "eye_health_report__hospital_released_version"
        ).filter(eye_health_report__hospital_released_at__isnull=False).order_by("-created_at").first()
        if not encounter:
            return None
        report = getattr(encounter, "eye_health_report", None)
        if not report or not report.hospital_released_version_id:
            return None
        return report

    def _targeted_url(self, obj, audience):
        report = self._released_targeted_report(obj)
        if not report:
            return ""
        path = f"/api/reports/eye-health/{report.pk}/pdf/?report_format={audience}"
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    def get_targeted_patient_report_url(self, obj):
        return self._targeted_url(obj, "patient")

    def get_targeted_clinician_report_url(self, obj):
        return self._targeted_url(obj, "clinician")

    def _combined_url(self, obj, audience):
        report = self._released_targeted_report(obj)
        encounter = report.encounter if report else None
        if not encounter or not encounter.includes_diabetic_screening or not obj.report_id:
            return ""
        path = f"/api/reports/eye-health/combined/{encounter.pk}/bundle/?report_format={audience}"
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    def get_combined_patient_bundle_url(self, obj):
        return self._combined_url(obj, "patient")

    def get_combined_clinician_bundle_url(self, obj):
        return self._combined_url(obj, "clinician")

    def get_latest_payment(self, obj):
        try:
            return obj.ops_payments.order_by("-created_at").first()
        except Exception:
            return None

    def get_payment_status(self, obj):
        payment = self.get_latest_payment(obj)
        return payment.status if payment else "not_created"

    def get_payment_amount(self, obj):
        payment = self.get_latest_payment(obj)
        return str(payment.amount) if payment else ""

    def get_payment_currency(self, obj):
        payment = self.get_latest_payment(obj)
        return payment.currency if payment else "NGN"

    def get_payment_reference(self, obj):
        payment = self.get_latest_payment(obj)
        if not payment:
            return ""
        return payment.paystack_reference or payment.payment_id or ""

    def get_payment_paid_at(self, obj):
        payment = self.get_latest_payment(obj)
        if not payment or not payment.paid_at:
            return ""
        return payment.paid_at
