from rest_framework.permissions import BasePermission, SAFE_METHODS


ALLOWED_PATIENT_WRITE_ROLES = {
    "sentinel_ops",
    "super_admin",
    "clinic_admin",
    "clinic_screener",
}


class CanManagePatients(BasePermission):
    """
    Authenticated users can read patients.
    sentinel_ops, super_admin, clinic_admin, and clinic_screener
    can create/update/delete patients.
    """

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if request.method in SAFE_METHODS:
            return True

        if user.is_superuser:
            return True

        user_roles = set(user.groups.values_list("name", flat=True))
        return bool(user_roles.intersection(ALLOWED_PATIENT_WRITE_ROLES))