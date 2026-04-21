from django.contrib import admin
from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "clinic_id",
        "organization_type",
        "contact_email",
        "is_active",
        "created_at",
    )
    list_filter = ("organization_type", "is_active")
    search_fields = ("name", "clinic_id", "contact_email")