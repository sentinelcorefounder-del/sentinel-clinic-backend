from django.contrib import admin
from .models import ConsentRecord


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    list_display = (
        "consent_id",
        "patient",
        "encounter",
        "consent_type",
        "consent_status",
        "consent_date",
        "captured_by",
    )
    search_fields = ("consent_id", "patient__patient_id", "patient__first_name", "patient__last_name")
    list_filter = ("consent_type", "consent_status", "consent_date")