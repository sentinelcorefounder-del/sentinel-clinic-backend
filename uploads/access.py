from organizations.services.branches import get_main_branch
from reports.permissions import has_internal_ops_authority
from reports.release_control import is_report_released_to_hospital


CLINIC_ASSET_ROLES = {
    "clinic_admin",
    "clinic_screener",
    "optometrist",
    "ophthalmologist",
    "reviewer",
}
HOSPITAL_ASSET_ROLES = {"hospital_admin"}


def _has_clinic_branch_access(user, organization, branch):
    if not organization or not branch or branch.organization_id != organization.id:
        return False
    access = user.branch_access.filter(branch__organization=organization)
    if access.filter(branch=branch).exists() or access.filter(
        has_all_branch_access=True
    ).exists():
        return True
    if access.exists():
        return False
    main_branch = get_main_branch(organization)
    return bool(main_branch and main_branch.id == branch.id)


def can_access_clinical_asset(user, *, encounter, organization, branch):
    roles = set(user.groups.values_list("name", flat=True))
    linked = getattr(user, "organization_link", None)
    referral = getattr(encounter, "hospital_referral", None)
    report = getattr(referral, "report", None) if referral else None

    internal_access = has_internal_ops_authority(user)
    clinic_access = bool(
        not user.is_superuser
        and roles & CLINIC_ASSET_ROLES
        and linked
        and organization
        and linked.organization_id == organization.id
        and _has_clinic_branch_access(user, organization, branch)
    )
    hospital_access = bool(
        not user.is_superuser
        and roles & HOSPITAL_ASSET_ROLES
        and linked
        and referral
        and referral.source_hospital_id == linked.organization_id
        and is_report_released_to_hospital(report, referral)
    )
    return internal_access or clinic_access or hospital_access
