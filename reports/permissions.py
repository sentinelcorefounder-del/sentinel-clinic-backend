from rest_framework.permissions import BasePermission, SAFE_METHODS


CLINICAL_REPORT_ROLES = {"optometrist", "reviewer"}
OPS_REVIEW_ROLES = {"ops_admin", "sentinel_ops"}


def _roles(user):
    if not user or not user.is_authenticated:
        return set()
    return set(user.groups.values_list("name", flat=True))


def _is_internal(user):
    return bool(
        getattr(getattr(user, "security_profile", None), "is_internal_sentinel_staff", False)
    )


class CanManageReports(BasePermission):
    """Read access is queryset-scoped; writes require an exact clinical role."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return bool(_roles(user) & CLINICAL_REPORT_ROLES)


class CanSubmitReportToOps(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and _roles(user) & CLINICAL_REPORT_ROLES)


class CanReviewOpsReports(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and _is_internal(user)
            and _roles(user) & OPS_REVIEW_ROLES
        )
