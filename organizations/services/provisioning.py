from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from organizations.models import Organization
from users.models import UserOrganization

User = get_user_model()


@transaction.atomic
def provision_clinic_with_admin(payload):
    organization, organization_created = Organization.objects.update_or_create(
        clinic_id=payload["clinic_id"],
        defaults={
            "name": payload["name"],
            "contact_email": payload.get("contact_email", ""),
            "phone": payload.get("phone", ""),
            "address": payload.get("address", ""),
            "report_signatory_name": payload.get("report_signatory_name", ""),
            "report_signatory_title": payload.get("report_signatory_title", ""),
            "report_signatory_odorbn": payload.get("report_signatory_odorbn", ""),
            "report_footer_note": payload.get("report_footer_note", ""),
            "is_active": payload.get("is_active", True),
        },
    )

    user, user_created = User.objects.get_or_create(
        username=payload["admin_username"],
        defaults={
            "email": payload.get("admin_email", ""),
            "first_name": payload.get("admin_first_name", ""),
            "last_name": payload.get("admin_last_name", ""),
            "is_active": True,
        },
    )

    if payload.get("admin_email"):
        user.email = payload.get("admin_email", user.email)

    if payload.get("admin_first_name"):
        user.first_name = payload.get("admin_first_name", user.first_name)

    if payload.get("admin_last_name"):
        user.last_name = payload.get("admin_last_name", user.last_name)

    user.is_active = True

    if user_created and payload.get("temporary_password"):
        user.set_password(payload["temporary_password"])

    user.save()

    UserOrganization.objects.get_or_create(
        user=user,
        organization=organization,
    )

    role_name = payload.get("admin_role", "clinic_admin")
    group, _ = Group.objects.get_or_create(name=role_name)
    user.groups.add(group)

    return {
        "organization_id": organization.id,
        "clinic_id": organization.clinic_id,
        "organization_created": organization_created,
        "user_id": user.id,
        "username": user.username,
        "user_created": user_created,
        "role": role_name,
    }