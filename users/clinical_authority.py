CLINICAL_AUTHOR_ROLES = ("optometrist", "reviewer")


def exact_clinical_authority(user):
    """Return the user's exact clinical-author role, without granting scope."""
    if not user or not user.is_authenticated or user.is_superuser:
        return ""
    roles = set(user.groups.values_list("name", flat=True))
    return next((role for role in CLINICAL_AUTHOR_ROLES if role in roles), "")


def normalized_professional_credentials(
    *, clinician_name, professional_role, registration_number, fallback_name="",
    fallback_role="",
):
    values = (
        (clinician_name or fallback_name or "").strip(),
        (professional_role or fallback_role or "").strip(),
        (registration_number or "").strip(),
    )
    return values if all(values) else None
