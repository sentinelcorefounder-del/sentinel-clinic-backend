from rest_framework import serializers

from referrals.models import HospitalReferral
from reports.models import StructuredReport
from .models import OpsAuditLog, OpsNotification, OpsPayment


class OpsPaymentSerializer(serializers.ModelSerializer):
    referral_id_display = serializers.CharField(source="referral.referral_id", read_only=True)
    patient_name = serializers.SerializerMethodField()
    source_hospital_name = serializers.CharField(source="referral.source_hospital.name", read_only=True)
    matched_clinic_name = serializers.CharField(source="referral.matched_clinic.name", read_only=True)

    class Meta:
        model = OpsPayment
        fields = [
            "id",
            "referral",
            "referral_id_display",
            "patient_name",
            "source_hospital_name",
            "matched_clinic_name",
            "payment_id",
            "patient_email",
            "amount",
            "currency",
            "paystack_reference",
            "payment_link",
            "status",
            "amount_received",
            "paid_at",
            "internal_notes",
            "created_at",
            "updated_at",
        ]

    def get_patient_name(self, obj):
        return f"{obj.referral.first_name} {obj.referral.last_name}".strip()


class OpsReferralSerializer(serializers.ModelSerializer):
    source_hospital_name = serializers.CharField(source="source_hospital.name", read_only=True)
    matched_clinic_name = serializers.CharField(source="matched_clinic.name", read_only=True)
    patient_name = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    payment_link = serializers.SerializerMethodField()
    payment_id = serializers.SerializerMethodField()

    class Meta:
        model = HospitalReferral
        fields = [
            "id",
            "referral_id",
            "patient_name",
            "first_name",
            "last_name",
            "dob",
            "patient_sex",
            "phone_number",
            "email",
            "source_hospital",
            "source_hospital_name",
            "matched_clinic",
            "matched_clinic_name",
            "reason_for_referral",
            "diabetes_type",
            "hospital_mrn",
            "referral_date",
            "referral_status",
            "report_ready",
            "report",
            "payout_status",
            "hospital_commission_amount",
            "payout_date",
            "notes",
            "payment_status",
            "payment_link",
            "payment_id",
            "created_at",
            "updated_at",
        ]

    def get_patient_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()

    def get_latest_payment(self, obj):
        return obj.ops_payments.order_by("-created_at").first()

    def get_payment_status(self, obj):
        payment = self.get_latest_payment(obj)
        return payment.status if payment else ""

    def get_payment_link(self, obj):
        payment = self.get_latest_payment(obj)
        return payment.payment_link if payment else ""

    def get_payment_id(self, obj):
        payment = self.get_latest_payment(obj)
        return payment.id if payment else None


class OpsReportSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()
    clinic_name = serializers.CharField(source="patient.assigned_clinic.name", read_only=True)
    encounter_id_display = serializers.CharField(source="encounter.encounter_id", read_only=True)
    submitted_to_ops_by_display = serializers.SerializerMethodField()
    ops_reviewed_by_display = serializers.SerializerMethodField()

    class Meta:
        model = StructuredReport
        fields = [
            "id",
            "report_id",
            "patient",
            "patient_name",
            "clinic_name",
            "encounter",
            "encounter_id_display",
            "review_date",
            "report_status",
            "urgency_outcome",
            "recommendation",
            "submitted_to_ops_at",
            "submitted_to_ops_by_display",
            "ops_reviewed_at",
            "ops_reviewed_by_display",
            "ops_review_note",
            "payout_email_sent_at",
            "created_at",
            "updated_at",
        ]

    def get_patient_name(self, obj):
        return f"{obj.patient.first_name} {obj.patient.last_name}".strip()

    def get_submitted_to_ops_by_display(self, obj):
        user = obj.submitted_to_ops_by
        return user.username if user else ""

    def get_ops_reviewed_by_display(self, obj):
        user = obj.ops_reviewed_by
        return user.username if user else ""


class OpsAuditLogSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()

    class Meta:
        model = OpsAuditLog
        fields = [
            "id",
            "actor",
            "actor_display",
            "action",
            "entity_type",
            "entity_id",
            "entity_label",
            "message",
            "metadata",
            "created_at",
        ]

    def get_actor_display(self, obj):
        if not obj.actor:
            return "System"
        return obj.actor.username or obj.actor.email or str(obj.actor)
    
class OpsNotificationSerializer(serializers.ModelSerializer):
    created_by_display = serializers.SerializerMethodField()

    class Meta:
        model = OpsNotification
        fields = [
            "id",
            "title",
            "message",
            "level",
            "entity_type",
            "entity_id",
            "entity_label",
            "is_read",
            "created_by",
            "created_by_display",
            "created_at",
            "read_at",
        ]

    def get_created_by_display(self, obj):
        if not obj.created_by:
            return "System"
        return obj.created_by.username or obj.created_by.email or str(obj.created_by)