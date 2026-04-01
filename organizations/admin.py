from django.contrib import admin
from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("clinic_id", "name", "contact_email", "is_active", "created_at")
    search_fields = ("clinic_id", "name", "contact_email")
    list_filter = ("is_active",)