from rest_framework.exceptions import PermissionDenied

from common.tenant import get_user_organization
from organizations.services.branches import user_can_access_branch


def role_names(user):
    return set(user.groups.values_list("name", flat=True)) if user and user.is_authenticated else set()


def require_optometrist(user, clinic, branch):
    if not user or not user.is_authenticated or user.is_superuser:
        raise PermissionDenied("Exact optometrist authority is required.")
    if "optometrist" not in role_names(user):
        raise PermissionDenied("Exact optometrist authority is required.")
    organization = get_user_organization(user)
    if not organization or organization.id != clinic.id:
        raise PermissionDenied("The optometrist is outside the performing clinic.")
    if not user_can_access_branch(user, branch):
        raise PermissionDenied("The optometrist cannot access this branch.")


def is_clinic_admin(user, clinic, branch):
    if not user or not user.is_authenticated or user.is_superuser:
        return False
    organization = get_user_organization(user)
    return bool(
        organization and organization.id == clinic.id
        and "clinic_admin" in role_names(user)
        and user_can_access_branch(user, branch)
    )


def can_distribute(user, referral):
    responsibility = getattr(referral.encounter, "onward_responsibility", None)
    if responsibility and responsibility.optometrist_id == getattr(user, "id", None):
        require_optometrist(user, referral.originating_clinic, referral.branch)
        return True
    return is_clinic_admin(user, referral.originating_clinic, referral.branch) and (
        "onward_referral_distributor" in role_names(user)
    )


def require_current_author(user, referral):
    responsibility = getattr(referral.encounter, "onward_responsibility", None)
    if not responsibility or responsibility.optometrist_id != getattr(user, "id", None):
        raise PermissionDenied("Only the responsible optometrist may perform this clinical action.")
    require_optometrist(user, referral.originating_clinic, referral.branch)


def clinic_can_view(user, referral):
    if not user or not user.is_authenticated or user.is_superuser:
        return False
    organization = get_user_organization(user)
    if not organization or organization.id != referral.originating_clinic_id:
        return False
    if not user_can_access_branch(user, referral.branch):
        return False
    return bool(role_names(user) & {"optometrist", "clinic_admin"})


def clinic_can_view_encounter(user, encounter):
    clinic = encounter.patient.assigned_clinic
    branch = encounter.service_branch or encounter.patient.assigned_branch
    if not clinic or not branch or not user or not user.is_authenticated or user.is_superuser:
        return False
    organization = get_user_organization(user)
    return bool(
        organization and organization.id == clinic.id
        and user_can_access_branch(user, branch)
        and role_names(user) & {"optometrist", "clinic_admin"}
    )


def hospital_can_view(user, version):
    if not user or not user.is_authenticated or user.is_superuser:
        return False
    organization = get_user_organization(user)
    if not organization or organization.organization_type != "hospital":
        return False
    if "hospital_admin" not in role_names(user):
        return False
    return version.availabilities.filter(
        recipient_organization=organization, state__in={"active", "superseded"}
    ).exists()
