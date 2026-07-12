from django.contrib import admin

from .models import PatientTimelineEvent


@admin.register(PatientTimelineEvent)
class PatientTimelineEventAdmin(admin.ModelAdmin):
    list_display = (
        "patient",
        "category",
        "event_type",
        "title",
        "organization",
        "occurred_at",
    )
    list_filter = ("category", "event_type", "visibility", "organization")
    search_fields = (
        "patient__patient_id",
        "patient__first_name",
        "patient__last_name",
        "title",
        "description",
        "report_id",
        "referral_id",
        "payment_id",
    )
    readonly_fields = (
        "event_key",
        "patient",
        "category",
        "event_type",
        "title",
        "description",
        "source_type",
        "source_id",
        "encounter_id",
        "report_id",
        "referral_id",
        "payment_id",
        "actor",
        "organization",
        "visibility",
        "metadata",
        "occurred_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
