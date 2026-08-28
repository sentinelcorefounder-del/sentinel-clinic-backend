from reports.permissions import has_internal_ops_authority
from reports.release_control import is_report_released_to_hospital


CLINICAL_ASSET_ROLES = {"optometrist", "ophthalmologist", "reviewer"}
HOSPITAL_ASSET_ROLES = {"hospital_admin"}


def can_access_clinical_asset(user, *, encounter, organization, branch):
    roles = set(user.groups.values_list("name", flat=True))
    linked = getattr(user, "organization_link", None)
    referral = getattr(encounter, "hospital_referral", None)
    report = getattr(referral, "report", None) if referral else None

    internal_access = has_internal_ops_authority(user)
    explicit_branch_access = bool(branch) and (
        user.branch_access.filter(branch=branch).exists()
        or user.branch_access.filter(
            branch__organization=organization,
            has_all_branch_access=True,
        ).exists()
    )
    clinic_access = bool(
        not user.is_superuser
        and roles & CLINICAL_ASSET_ROLES
        and linked
        and organization
        and linked.organization_id == organization.id
        and explicit_branch_access
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
