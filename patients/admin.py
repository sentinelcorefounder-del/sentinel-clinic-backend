from django.contrib import admin
from .models import Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("patient_id", "first_name", "last_name", "sex", "phone", "created_at")
    search_fields = ("patient_id", "first_name", "last_name", "phone", "email")