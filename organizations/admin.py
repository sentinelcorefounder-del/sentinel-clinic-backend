from django.contrib import admin

from .models import Organization, OrganizationProfile, PartnerNotification


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = (
        "clinic_id",
        "name",
        "organization_type",
        "is_active",
        "contact_email",
    )
    list_filter = ("organization_type", "is_active")
    search_fields = ("clinic_id", "name", "contact_email")


@admin.register(OrganizationProfile)
class OrganizationProfileAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "workflow_mode",
        "referral_requirement",
        "patient_ownership",
        "sentinel_review_policy",
        "subscription_tier",
    )
    list_filter = (
        "workflow_mode",
        "referral_requirement",
        "patient_ownership",
        "sentinel_review_policy",
        "branding_policy",
        "subscription_tier",
    )
    search_fields = (
        "organization__name",
        "organization__clinic_id",
    )


@admin.register(PartnerNotification)
class PartnerNotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "recipient", "notification_type", "is_read", "created_at")
    list_filter = ("notification_type", "level", "is_read", "organization__organization_type")
    search_fields = ("title", "message", "recipient__username", "organization__name", "entity_id")
    readonly_fields = ("created_at", "read_at")
