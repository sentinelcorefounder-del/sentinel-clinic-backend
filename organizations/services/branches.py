from organizations.models import OrganizationBranch


def get_main_branch(organization):
    if not organization:
        return None
    return (
        organization.branches.filter(is_head_office=True, is_active=True).first()
        or organization.branches.filter(is_active=True).first()
    )


def get_user_default_branch(user, organization=None):
    access = (
        user.branch_access.select_related("branch")
        .filter(branch__is_active=True, is_default=True)
        .first()
    )
    if access and (not organization or access.branch.organization_id == organization.id):
        return access.branch
    if organization:
        all_access = user.branch_access.filter(
            branch__organization=organization,
            has_all_branch_access=True,
        ).exists()
        if all_access or not user.branch_access.exists():
            return get_main_branch(organization)
    return None


def user_can_access_branch(user, branch):
    if not branch:
        return True
    if user.is_superuser or user.groups.filter(name="ops_admin").exists():
        return True
    return user.branch_access.filter(
        branch__organization=branch.organization,
    ).filter(
        branch=branch,
    ).exists() or user.branch_access.filter(
        branch__organization=branch.organization,
        has_all_branch_access=True,
    ).exists()


def accessible_branch_ids(user, organization):
    if user.is_superuser or user.groups.filter(name="ops_admin").exists():
        return None
    access = user.branch_access.filter(branch__organization=organization)
    if access.filter(has_all_branch_access=True).exists():
        return None
    ids = list(access.values_list("branch_id", flat=True))
    # Legacy users created before the branch migration are kept functional
    # against the main branch until Ops explicitly changes their scope.
    if not ids:
        main = get_main_branch(organization)
        return [main.id] if main else []
    return ids
