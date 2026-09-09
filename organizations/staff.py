from django.conf import settings
from django.contrib.auth.models import Group, User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import ClinicalProfessionalProfile, UserBranchAccess, UserOrganization, UserSecurityProfile
from reports.permissions import has_internal_ops_authority
from .models import Organization, OrganizationBranch


CLINIC_ASSIGNABLE_ROLES = {
    "clinic_admin",
    "clinic_screener",
    "optometrist",
    "reviewer",
    "clinic_owner_optometrist",
}
CLINIC_ADMIN_ROLES = {"clinic_admin", "clinic_owner_optometrist"}
OPS_ROLES = {"ops_admin", "sentinel_ops"}


def _roles(user):
    return set(user.groups.values_list("name", flat=True))


def _explicit_organization(user):
    try:
        return user.organization_link.organization
    except (AttributeError, UserOrganization.DoesNotExist):
        return None


def _can_manage_organization(user, organization):
    roles = _roles(user)
    if has_internal_ops_authority(user):
        return True
    explicit = _explicit_organization(user)
    return bool(
        organization.organization_type == "clinic"
        and explicit
        and explicit.id == organization.id
        and roles & CLINIC_ADMIN_ROLES
    )


def _can_verify_profiles(user):
    return has_internal_ops_authority(user)


def _serialize_profile(user):
    profile = getattr(user, "clinical_professional_profile", None)
    if not profile:
        return None
    return {
        "display_name": profile.display_name,
        "professional_role": profile.professional_role,
        "registration_number": profile.registration_number,
        "registration_body": profile.registration_body,
        "qualifications": profile.qualifications,
        "signature_name": profile.signature_name or profile.display_name,
        "is_verified": profile.is_verified,
        "verified_at": profile.verified_at,
        "updated_at": profile.updated_at,
    }


def _serialize_staff(user, organization):
    access_rows = list(
        UserBranchAccess.objects.select_related("branch")
        .filter(user=user, branch__organization=organization, branch__is_active=True)
        .order_by("branch__name", "branch__id")
    )
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "is_active": user.is_active,
        "roles": sorted(_roles(user) & CLINIC_ASSIGNABLE_ROLES),
        "all_branch_access": any(row.has_all_branch_access for row in access_rows),
        "branch_ids": [row.branch_id for row in access_rows],
        "branches": [
            {"id": row.branch_id, "name": row.branch.name, "branch_code": row.branch.branch_code}
            for row in access_rows
        ],
        "clinical_profile": _serialize_profile(user),
    }


def _activation_link(user):
    frontend_base = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{frontend_base}/reset-password?uid={uid}&token={token}"


def _send_activation(user, organization):
    if not user.email:
        return False
    link = _activation_link(user)
    send_mail(
        subject="Activate your Sentinel Clinic Portal account",
        message=(
            f"Hello {user.first_name or user.username},\n\n"
            f"An account has been created for you at {organization.name}.\n\n"
            f"Username: {user.username}\n\n"
            f"Set your password using this link:\n{link}\n\n"
            "If you did not expect this email, contact your clinic administrator or Sentinel Ops.\n"
        ),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[user.email],
        fail_silently=False,
    )
    return True


def _validate_roles(request_user, requested_roles):
    if not isinstance(requested_roles, list):
        return None, "roles must be a list."
    roles = {str(role).strip() for role in requested_roles if str(role).strip()}
    invalid = roles - CLINIC_ASSIGNABLE_ROLES
    if invalid:
        return None, "Unsupported clinic role(s): " + ", ".join(sorted(invalid))
    # The combined clinic-owner/clinical role is a provisioning/ownership role;
    # ordinary clinic admins cannot create another owner-equivalent account.
    if "clinic_owner_optometrist" in roles and not _can_verify_profiles(request_user):
        return None, "Only Sentinel Ops can assign the clinic owner optometrist role."
    return roles, ""


def _apply_branch_access(user, organization, *, all_branch_access, branch_ids):
    active_branches = list(organization.branches.filter(is_active=True).order_by("id"))
    if not active_branches:
        raise ValueError("This clinic has no active branches.")

    if all_branch_access:
        selected = active_branches[:1]
    else:
        try:
            ids = {int(value) for value in (branch_ids or [])}
        except (TypeError, ValueError):
            raise ValueError("branch_ids must contain valid branch IDs.")
        selected = [branch for branch in active_branches if branch.id in ids]
        if not selected:
            raise ValueError("Select at least one active clinic branch or grant all-branch access.")
        if len(selected) != len(ids):
            raise ValueError("One or more selected branches do not belong to this clinic.")

    UserBranchAccess.objects.filter(user=user).delete()
    for index, branch in enumerate(selected):
        UserBranchAccess.objects.create(
            user=user,
            branch=branch,
            has_all_branch_access=bool(all_branch_access and index == 0),
            is_default=index == 0,
        )


def _apply_roles(user, roles):
    managed_groups = Group.objects.filter(name__in=CLINIC_ASSIGNABLE_ROLES)
    user.groups.remove(*managed_groups)
    for role in sorted(roles):
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)


class OrganizationStaffListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def _organization(self, request, organization_id):
        organization = Organization.objects.filter(pk=organization_id, organization_type="clinic", is_active=True).first()
        if not organization or not _can_manage_organization(request.user, organization):
            return None
        return organization

    def get(self, request, organization_id):
        organization = self._organization(request, organization_id)
        if not organization:
            return Response({"detail": "You cannot manage staff for this clinic."}, status=status.HTTP_403_FORBIDDEN)
        users = User.objects.filter(organization_link__organization=organization).order_by("username")
        return Response({
            "organization": {"id": organization.id, "name": organization.name, "clinic_id": organization.clinic_id},
            "assignable_roles": sorted(CLINIC_ASSIGNABLE_ROLES),
            "can_verify_profiles": _can_verify_profiles(request.user),
            "branches": [
                {"id": branch.id, "name": branch.name, "branch_code": branch.branch_code}
                for branch in organization.branches.filter(is_active=True).order_by("name", "id")
            ],
            "staff": [_serialize_staff(user, organization) for user in users],
        })

    @transaction.atomic
    def post(self, request, organization_id):
        organization = self._organization(request, organization_id)
        if not organization:
            return Response({"detail": "You cannot manage staff for this clinic."}, status=status.HTTP_403_FORBIDDEN)

        username = str(request.data.get("username") or "").strip()
        email = str(request.data.get("email") or "").strip()
        if not username or not email:
            return Response({"detail": "Username and email are required."}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(username__iexact=username).exists():
            return Response({"detail": "That username is already in use."}, status=status.HTTP_409_CONFLICT)
        if User.objects.filter(email__iexact=email).exists():
            return Response({"detail": "That email address is already linked to an account."}, status=status.HTTP_409_CONFLICT)

        roles, error = _validate_roles(request.user, request.data.get("roles", []))
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        user = User(
            username=username,
            email=email,
            first_name=str(request.data.get("first_name") or "").strip(),
            last_name=str(request.data.get("last_name") or "").strip(),
            is_active=True,
        )
        user.set_unusable_password()
        user.save()
        UserOrganization.objects.create(user=user, organization=organization)
        UserSecurityProfile.objects.create(user=user, must_change_password=True)
        _apply_roles(user, roles)
        try:
            _apply_branch_access(
                user,
                organization,
                all_branch_access=bool(request.data.get("all_branch_access", False)),
                branch_ids=request.data.get("branch_ids", []),
            )
        except ValueError as exc:
            transaction.set_rollback(True)
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        email_sent = _send_activation(user, organization)
        return Response({"staff": _serialize_staff(user, organization), "email_sent": email_sent}, status=status.HTTP_201_CREATED)


class OrganizationStaffDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _objects(self, request, organization_id, user_id):
        organization = Organization.objects.filter(pk=organization_id, organization_type="clinic", is_active=True).first()
        if not organization or not _can_manage_organization(request.user, organization):
            return None, None
        user = User.objects.filter(pk=user_id, organization_link__organization=organization).first()
        return organization, user

    @transaction.atomic
    def patch(self, request, organization_id, user_id):
        organization, user = self._objects(request, organization_id, user_id)
        if not organization:
            return Response({"detail": "You cannot manage staff for this clinic."}, status=status.HTTP_403_FORBIDDEN)
        if not user:
            return Response({"detail": "Staff member not found in this clinic."}, status=status.HTTP_404_NOT_FOUND)

        if "roles" in request.data:
            roles, error = _validate_roles(request.user, request.data.get("roles"))
            if error:
                return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)
            # Prevent a clinic administrator from removing their own last admin role.
            if user.id == request.user.id and not (roles & CLINIC_ADMIN_ROLES) and not has_internal_ops_authority(request.user):
                return Response({"detail": "You cannot remove your own clinic-administrator authority."}, status=status.HTTP_400_BAD_REQUEST)
            _apply_roles(user, roles)

        if "all_branch_access" in request.data or "branch_ids" in request.data:
            try:
                _apply_branch_access(
                    user,
                    organization,
                    all_branch_access=bool(request.data.get("all_branch_access", False)),
                    branch_ids=request.data.get("branch_ids", []),
                )
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        mutable_user_fields = {"first_name", "last_name", "email", "is_active"}
        changed = []
        for field in mutable_user_fields:
            if field not in request.data:
                continue
            value = request.data[field]
            if field in {"first_name", "last_name", "email"}:
                value = str(value or "").strip()
            if field == "email" and value and User.objects.filter(email__iexact=value).exclude(pk=user.pk).exists():
                return Response({"detail": "That email address is already linked to another account."}, status=status.HTTP_409_CONFLICT)
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed.append(field)
        if changed:
            user.save(update_fields=changed)

        if "clinical_profile_verified" in request.data:
            if not _can_verify_profiles(request.user):
                return Response({"detail": "Only Sentinel Ops can verify clinical professional profiles."}, status=status.HTTP_403_FORBIDDEN)
            profile = getattr(user, "clinical_professional_profile", None)
            if not profile:
                return Response({"detail": "The clinician has not completed a professional profile."}, status=status.HTTP_400_BAD_REQUEST)
            verified = bool(request.data.get("clinical_profile_verified"))
            profile.is_verified = verified
            profile.verified_at = timezone.now() if verified else None
            profile.save(update_fields=["is_verified", "verified_at", "updated_at"])

        return Response({"staff": _serialize_staff(user, organization)})
