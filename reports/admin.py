from django.contrib import admin
from .models import StructuredReport


@admin.register(StructuredReport)
class StructuredReportAdmin(admin.ModelAdmin):
    list_display = (
        "report_id",
        "encounter",
        "patient",
        "review_date",
        "urgency_outcome",
        "report_status",
        "created_at",
    )
    search_fields = ("report_id", "encounter__encounter_id", "patient__patient_id")
    list_filter = ("urgency_outcome", "report_status", "ungradable")