from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from organizations.models import Organization
from users.models import UserOrganization, UserSecurityProfile

User = get_user_model()


def _build_activation_link(user):
    frontend_base = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{frontend_base}/reset-password?uid={uid}&token={token}"


def _send_startup_email(user, organization_name, activation_link, portal_label):
    if not user.email:
        return False

    subject = f"Activate your {portal_label} account"
    message = (
        f"Hello {user.first_name or user.username},\n\n"
        f"Your Sentinel account for {organization_name} has been created.\n\n"
        f"Username: {user.username}\n\n"
        f"Please click the activation link below to set your password and access your portal:\n\n"
        f"{activation_link}\n\n"
        f"For security, this link should only be used by the intended recipient.\n\n"
        f"If you did not expect this email, please contact Sentinel Ops.\n\n"
        f"Thank you,\n"
        f"Sentinel Health"
    )

    send_mail(
        subject=subject,
        message=message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[user.email],
        fail_silently=False,
    )
    return True


def _prepare_user_for_activation(user, payload):
    if payload.get("admin_email"):
        user.email = payload.get("admin_email", user.email)

    if payload.get("admin_first_name"):
        user.first_name = payload.get("admin_first_name", user.first_name)

    if payload.get("admin_last_name"):
        user.last_name = payload.get("admin_last_name", user.last_name)

    user.is_active = True

    profile, _ = UserSecurityProfile.objects.get_or_create(user=user)
    profile.must_change_password = True

    # The activation/reset-password email is the main onboarding method.
    # Temporary password is optional fallback only.
    if payload.get("temporary_password"):
        user.set_password(payload["temporary_password"])
    else:
        user.set_unusable_password()

    user.save()
    profile.save()

    return profile


@transaction.atomic
def provision_clinic_with_admin(payload):
    organization, organization_created = Organization.objects.update_or_create(
        clinic_id=payload["clinic_id"],
        defaults={
            "name": payload["name"],
            "organization_type": "clinic",
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

    _prepare_user_for_activation(user, payload)

    UserOrganization.objects.update_or_create(
        user=user,
        defaults={"organization": organization},
    )

    role_name = payload.get("admin_role", "clinic_admin")
    group, _ = Group.objects.get_or_create(name=role_name)
    user.groups.add(group)

    activation_link = _build_activation_link(user)
    email_sent = _send_startup_email(
        user=user,
        organization_name=organization.name,
        activation_link=activation_link,
        portal_label="Sentinel Clinic Portal",
    )

    return {
        "organization_id": organization.id,
        "clinic_id": organization.clinic_id,
        "organization_created": organization_created,
        "user_id": user.id,
        "username": user.username,
        "user_created": user_created,
        "role": role_name,
        "activation_link": activation_link,
        "email_sent": email_sent,
    }


@transaction.atomic
def provision_hospital_with_admin(payload):
    organization, organization_created = Organization.objects.update_or_create(
        clinic_id=payload["hospital_id"],
        defaults={
            "name": payload["hospital_name"],
            "organization_type": "hospital",
            "contact_email": payload.get("contact_email", ""),
            "phone": payload.get("phone", ""),
            "address": payload.get("address", ""),
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

    _prepare_user_for_activation(user, payload)

    UserOrganization.objects.update_or_create(
        user=user,
        defaults={"organization": organization},
    )

    role_name = payload.get("admin_role", "hospital_admin")
    group, _ = Group.objects.get_or_create(name=role_name)
    user.groups.add(group)

    activation_link = _build_activation_link(user)
    email_sent = _send_startup_email(
        user=user,
        organization_name=organization.name,
        activation_link=activation_link,
        portal_label="Sentinel Hospital Portal",
    )

    return {
        "organization_id": organization.id,
        "hospital_id": organization.clinic_id,
        "organization_created": organization_created,
        "user_id": user.id,
        "username": user.username,
        "user_created": user_created,
        "role": role_name,
        "activation_link": activation_link,
        "email_sent": email_sent,
    }