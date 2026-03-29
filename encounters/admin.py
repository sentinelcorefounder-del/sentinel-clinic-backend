from django.contrib import admin
from .models import ScreeningEncounter


@admin.register(ScreeningEncounter)
class ScreeningEncounterAdmin(admin.ModelAdmin):
    list_display = ("encounter_id", "patient", "encounter_date", "screening_status", "created_at")
    search_fields = ("encounter_id", "patient__first_name", "patient__last_name", "patient__patient_id")
    list_filter = ("screening_status", "encounter_date")