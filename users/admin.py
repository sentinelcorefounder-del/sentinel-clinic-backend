from django.contrib import admin
from .models import UserOrganization, UserSecurityProfile


@admin.register(UserOrganization)
class UserOrganizationAdmin(admin.ModelAdmin):
    list_display = ("user", "organization")
    search_fields = ("user__username", "organization__name", "organization__clinic_id")


@admin.register(UserSecurityProfile)
class UserSecurityProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "must_change_password", "is_internal_sentinel_staff")
    list_filter = ("is_internal_sentinel_staff",)
    search_fields = ("user__username",)

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return ()
        return ("is_internal_sentinel_staff",)
