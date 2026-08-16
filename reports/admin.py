from django.contrib import admin

from .models import (
    ReportClinicalResponsibility,
    ReportStatusEvent,
    StructuredReport,
    StructuredReportVersion,
)


class ImmutableClinicalAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StructuredReport)
class StructuredReportAdmin(ImmutableClinicalAdmin):
    list_display = (
        "report_id", "encounter", "patient", "review_date", "urgency_outcome",
        "report_status", "lock_version", "created_at",
    )
    search_fields = ("report_id", "encounter__encounter_id", "patient__patient_id")
    list_filter = ("urgency_outcome", "report_status", "ungradable")


@admin.register(StructuredReportVersion)
class StructuredReportVersionAdmin(ImmutableClinicalAdmin):
    list_display = ("report", "version_number", "purpose", "editor", "created_at")


@admin.register(ReportStatusEvent)
class ReportStatusEventAdmin(ImmutableClinicalAdmin):
    list_display = ("report", "event_type", "actor", "created_at")


@admin.register(ReportClinicalResponsibility)
class ReportClinicalResponsibilityAdmin(ImmutableClinicalAdmin):
    list_display = ("report", "current_clinician", "authority_used", "accepted_at")
