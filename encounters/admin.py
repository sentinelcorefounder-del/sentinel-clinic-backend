from django.contrib import admin
from .models import (
    OcularAIReview,
    OcularDiagnosticAssessment,
    OcularInvestigation,
    ScreeningEncounter,
)


@admin.register(ScreeningEncounter)
class ScreeningEncounterAdmin(admin.ModelAdmin):
    list_display = ("encounter_id", "patient", "encounter_date", "screening_status", "created_at")
    search_fields = ("encounter_id", "patient__first_name", "patient__last_name", "patient__patient_id")
    list_filter = ("screening_status", "encounter_date")


@admin.register(OcularDiagnosticAssessment)
class OcularDiagnosticAssessmentAdmin(admin.ModelAdmin):
    list_display = ("encounter", "management_outcome", "completed_at", "updated_at")
    search_fields = (
        "encounter__encounter_id",
        "encounter__patient__patient_id",
        "impression",
    )


@admin.register(OcularInvestigation)
class OcularInvestigationAdmin(admin.ModelAdmin):
    list_display = (
        "investigation_id", "encounter", "investigation_type",
        "laterality", "reliability", "uploaded_at",
    )
    search_fields = (
        "investigation_id", "encounter__encounter_id",
        "encounter__patient__patient_id",
    )


@admin.register(OcularAIReview)
class OcularAIReviewAdmin(admin.ModelAdmin):
    list_display = (
        "review_id", "encounter", "status", "agreement_status",
        "expert_review_required", "clinician_decision", "requested_at",
    )
    search_fields = (
        "review_id", "encounter__encounter_id",
        "encounter__patient__patient_id",
    )
