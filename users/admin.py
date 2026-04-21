from django.contrib import admin
from .models import UserOrganization, UserSecurityProfile


@admin.register(UserOrganization)
class UserOrganizationAdmin(admin.ModelAdmin):
    list_display = ("user", "organization")
    search_fields = ("user__username", "organization__name", "organization__clinic_id")


@admin.register(UserSecurityProfile)
class UserSecurityProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "must_change_password")
    search_fields = ("user__username",)