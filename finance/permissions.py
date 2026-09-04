from rest_framework.permissions import BasePermission


INTERNAL_FINANCE_ROLES = {
    "administrator": "finance_admin",
    "operator": "finance_operator",
    "approver": "finance_approver",
}


def has_internal_finance_role(user, role):
    if not user or not user.is_authenticated:
        return False
    try:
        is_internal_staff = user.security_profile.is_internal_sentinel_staff
    except Exception:
        is_internal_staff = False
    if not is_internal_staff:
        return False
    return user.groups.filter(name=INTERNAL_FINANCE_ROLES[role]).exists()


class InternalFinancePermission(BasePermission):
    required_role = "administrator"
    message = "The required internal-finance role is not assigned."

    def has_permission(self, request, view):
        return has_internal_finance_role(request.user, self.required_role)


class IsInternalFinanceAdministrator(InternalFinancePermission):
    required_role = "administrator"


class IsInternalFinanceOperator(InternalFinancePermission):
    required_role = "operator"


class IsInternalFinanceApprover(InternalFinancePermission):
    required_role = "approver"


class IsInternalFinanceSessionManager(BasePermission):
    message = "An exact internal finance operator or finance administrator role is required."

    def has_permission(self, request, view):
        return has_internal_finance_role(request.user, "operator") or has_internal_finance_role(
            request.user, "administrator"
        )
