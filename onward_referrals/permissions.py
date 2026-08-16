from rest_framework.exceptions import PermissionDenied

from common.tenant import get_user_organization
from organizations.services.branches import user_can_access_branch
from users.clinical_authority import exact_clinical_authority


def role_names(user):
    return set(user.groups.values_list("name", flat=True)) if user and user.is_authenticated else set()


def require_clinical_author(user, clinic, branch):
    authority = exact_clinical_authority(user)
    if not authority:
        raise PermissionDenied("Exact optometrist or qualified reviewer authority is required.")
    organization = get_user_organization(user)
    if not organization or organization.id != clinic.id:
        raise PermissionDenied("The clinician is outside the performing clinic.")
    if not user_can_access_branch(user, branch):
        raise PermissionDenied("The clinician cannot access this branch.")
    return authority


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
        require_clinical_author(user, referral.originating_clinic, referral.branch)
        return True
    return is_clinic_admin(user, referral.originating_clinic, referral.branch) and (
        "onward_referral_distributor" in role_names(user)
    )


def require_current_author(user, referral):
    responsibility = getattr(referral.encounter, "onward_responsibility", None)
    if not responsibility or responsibility.optometrist_id != getattr(user, "id", None):
        raise PermissionDenied("Only the responsible clinical professional may perform this clinical action.")
    return require_clinical_author(user, referral.originating_clinic, referral.branch)


def clinical_capabilities(user, *, clinic, branch, responsibility=None):
    try:
        require_clinical_author(user, clinic, branch)
        can_accept = True
    except PermissionDenied:
        can_accept = False
    is_author = bool(
        can_accept and responsibility
        and responsibility.optometrist_id == getattr(user, "id", None)
    )
    return {
        "can_accept_responsibility": can_accept,
        "can_author": is_author,
        "can_administer_recipient": is_clinic_admin(user, clinic, branch),
    }


def clinic_can_view(user, referral):
    if not user or not user.is_authenticated or user.is_superuser:
        return False
    organization = get_user_organization(user)
    if not organization or organization.id != referral.originating_clinic_id:
        return False
    if not user_can_access_branch(user, referral.branch):
        return False
    return bool(role_names(user) & {"optometrist", "reviewer", "clinic_admin"})


def clinic_can_view_encounter(user, encounter):
    clinic = encounter.patient.assigned_clinic
    branch = encounter.service_branch or encounter.patient.assigned_branch
    if not clinic or not branch or not user or not user.is_authenticated or user.is_superuser:
        return False
    organization = get_user_organization(user)
    return bool(
        organization and organization.id == clinic.id
        and user_can_access_branch(user, branch)
        and role_names(user) & {"optometrist", "reviewer", "clinic_admin"}
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
