from django.contrib import admin
from .models import UserOrganization


@admin.register(UserOrganization)
class UserOrganizationAdmin(admin.ModelAdmin):
    list_display = ("user", "organization")
    search_fields = ("user__username", "organization__clinic_id", "organization__name")