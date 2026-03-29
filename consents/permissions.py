from rest_framework.permissions import BasePermission, SAFE_METHODS


ALLOWED_CONSENT_WRITE_ROLES = {"clinic_screener", "clinic_admin", "super_admin"}


class CanManageConsents(BasePermission):
    """
    Authenticated users can read consents.
    Only clinic_screener, clinic_admin, or superuser can create/update/delete consents.
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
        return bool(user_roles.intersection(ALLOWED_CONSENT_WRITE_ROLES))