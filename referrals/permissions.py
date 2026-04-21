from rest_framework.permissions import BasePermission


class IsHospitalUser(BasePermission):
    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        user_roles = set(user.groups.values_list("name", flat=True))
        return "hospital_admin" in user_roles