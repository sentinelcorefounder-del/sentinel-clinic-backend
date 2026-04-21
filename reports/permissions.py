from rest_framework.permissions import BasePermission, SAFE_METHODS


ALLOWED_REPORT_WRITE_ROLES = {"reviewer", "clinic_admin", "super_admin"}
OPS_REVIEW_ROLES = {"ops_admin", "super_admin"}


def _get_user_group_names(user):
    if not user or not user.is_authenticated:
        return set()
    return set(user.groups.values_list("name", flat=True))


class CanManageReports(BasePermission):
    """
    Authenticated users can read reports.
    Reviewer, clinic_admin, or superuser can create/update/delete reports.
    This supports:
    - small clinics where one user has clinic_admin and can do everything clinic-side
    - larger clinics where reviewer/other roles can be separated
    """

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if request.method in SAFE_METHODS:
            return True

        if user.is_superuser:
            return True

        user_roles = _get_user_group_names(user)
        return bool(user_roles.intersection(ALLOWED_REPORT_WRITE_ROLES))


class CanSubmitReportToOps(BasePermission):
    """
    Clinic-side submission permission.
    Small clinics can use clinic_admin for this.
    Larger clinics can use reviewer or clinic_admin.
    """

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        user_roles = _get_user_group_names(user)
        return bool(user_roles.intersection({"reviewer", "clinic_admin", "super_admin"}))


class CanReviewOpsReports(BasePermission):
    """
    Only Ops or superuser can approve/reject submitted reports.
    """

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        user_roles = _get_user_group_names(user)
        return bool(user_roles.intersection(OPS_REVIEW_ROLES))