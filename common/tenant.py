def get_user_organization(user):
    if getattr(user, "is_superuser", False):
        return None

    org_link = getattr(user, "organization_link", None)
    if org_link:
        return org_link.organization

    return None