from django.contrib import admin
from .models import HospitalReferral


@admin.register(HospitalReferral)
class HospitalReferralAdmin(admin.ModelAdmin):
    list_display = (
        "referral_id",
        "source_hospital",
        "first_name",
        "last_name",
        "patient_id_text",
        "matched_clinic",
        "referral_status",
        "report_ready",
        "payout_status",
        "created_at",
    )
    list_filter = (
        "referral_status",
        "report_ready",
        "payout_status",
        "source_hospital",
        "matched_clinic",
    )
    search_fields = (
        "referral_id",
        "patient_id_text",
        "first_name",
        "last_name",
        "source_hospital__name",
        "matched_clinic__name",
    )