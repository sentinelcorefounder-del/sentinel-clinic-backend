import getpass
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from organizations.models import Organization, OrganizationBranch
from users.models import UserBranchAccess, UserOrganization, UserSecurityProfile


DEMO_ACCOUNTS = (
    {
        "key": "OPS",
        "username": "staging_demo_ops",
        "email": "ops@sentinel-staging.example.invalid",
        "first_name": "Synthetic",
        "last_name": "Ops",
        "organization_code": "STG-SENTINEL-DEMO",
        "organization_name": "Synthetic Sentinel Staging",
        "organization_type": "sentinel",
        "roles": ("sentinel_ops",),
        "is_staff": True,
        "is_internal": True,
    },
    {
        "key": "CLINIC",
        "username": "staging_demo_clinic",
        "email": "clinic@sentinel-staging.example.invalid",
        "first_name": "Synthetic",
        "last_name": "Clinic Optometrist",
        "organization_code": "STG-CLINIC-DEMO",
        "organization_name": "Synthetic Small Eye Clinic",
        "organization_type": "clinic",
        "roles": ("clinic_admin", "optometrist"),
        "is_staff": False,
        "is_internal": False,
    },
    {
        "key": "REVIEWER",
        "username": "staging_demo_reviewer",
        "email": "reviewer@sentinel-staging.example.invalid",
        "first_name": "Synthetic",
        "last_name": "Qualified Reviewer",
        "organization_code": "STG-CLINIC-DEMO",
        "organization_name": "Synthetic Small Eye Clinic",
        "organization_type": "clinic",
        "roles": ("reviewer",),
        "is_staff": False,
        "is_internal": False,
    },
    {
        "key": "HOSPITAL",
        "username": "staging_demo_hospital",
        "email": "hospital@sentinel-staging.example.invalid",
        "first_name": "Synthetic",
        "last_name": "Hospital Referrer",
        "organization_code": "STG-HOSPITAL-DEMO",
        "organization_name": "Synthetic Referring Hospital",
        "organization_type": "hospital",
        "roles": ("hospital_admin",),
        "is_staff": False,
        "is_internal": False,
    },
)

PRODUCTION_HOSTS = {
    "api.usesentinelhealth.com",
    "sentinel-clinic-backend.onrender.com",
    "usesentinelhealth.com",
    "www.usesentinelhealth.com",
    "clinic.usesentinelhealth.com",
    "ops.usesentinelhealth.com",
}


class Command(BaseCommand):
    help = "Create or update synthetic staging-only organizations and demo users."

    def _validate_staging_environment(self):
        if os.environ.get("SENTINEL_STAGING_BOOTSTRAP_ENABLED", "").lower() != "true":
            raise CommandError("Staging demo bootstrap is not explicitly enabled.")

        expected_database = os.environ.get("SENTINEL_STAGING_DATABASE_NAME", "").strip()
        actual_database = str(connection.settings_dict.get("NAME") or "").strip()
        if (
            not expected_database
            or "staging" not in expected_database.lower()
            or expected_database != actual_database
        ):
            raise CommandError("The connected database is not the explicitly named staging database.")

        configured_hosts = {
            str(host).strip().lower()
            for host in getattr(settings, "ALLOWED_HOSTS", ())
            if str(host).strip()
        }
        frontend_host = str(getattr(settings, "FRONTEND_URL", "")).strip().lower()
        if configured_hosts & PRODUCTION_HOSTS or any(
            production_host in frontend_host for production_host in PRODUCTION_HOSTS
        ):
            raise CommandError("Production hosts are configured; staging bootstrap refused.")
        if not configured_hosts or not all("staging" in host for host in configured_hosts):
            raise CommandError("Every allowed host must be explicitly staging-labelled.")

    def _password(self, account):
        variable = f"SENTINEL_STAGING_{account['key']}_PASSWORD"
        password = os.environ.get(variable)
        if password:
            return password
        if not os.isatty(0):
            raise CommandError(f"{variable} must be provided in a non-interactive session.")
        password = getpass.getpass(f"Password for {account['username']}: ")
        if not password:
            raise CommandError(f"A password is required for {account['username']}.")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise CommandError("The interactive passwords did not match.")
        return password

    @transaction.atomic
    def handle(self, *args, **options):
        self._validate_staging_environment()
        passwords = {
            account["key"]: self._password(account)
            for account in DEMO_ACCOUNTS
        }
        User = get_user_model()

        organizations = {}
        for account in DEMO_ACCOUNTS:
            code = account["organization_code"]
            if code not in organizations:
                organization, _ = Organization.objects.update_or_create(
                    clinic_id=code,
                    defaults={
                        "name": account["organization_name"],
                        "organization_type": account["organization_type"],
                        "contact_email": "",
                        "address": "Synthetic staging data only",
                        "phone": "",
                        "is_active": True,
                    },
                )
                branch, _ = OrganizationBranch.objects.update_or_create(
                    organization=organization,
                    branch_code="MAIN",
                    defaults={
                        "name": "Synthetic Main Branch",
                        "address": "Synthetic staging data only",
                        "contact_email": "",
                        "phone": "",
                        "is_head_office": True,
                        "is_active": True,
                    },
                )
                organizations[code] = (organization, branch)

            organization, branch = organizations[code]
            user, _ = User.objects.update_or_create(
                username=account["username"],
                defaults={
                    "email": account["email"],
                    "first_name": account["first_name"],
                    "last_name": account["last_name"],
                    "is_active": True,
                    "is_staff": account["is_staff"],
                    "is_superuser": False,
                },
            )
            password = passwords[account["key"]]
            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                raise CommandError(
                    f"Password validation failed for {account['username']}."
                ) from exc
            user.set_password(password)
            user.save(update_fields=["password"])

            groups = [Group.objects.get_or_create(name=name)[0] for name in account["roles"]]
            user.groups.set(groups)
            UserOrganization.objects.update_or_create(
                user=user,
                defaults={"organization": organization},
            )
            UserBranchAccess.objects.update_or_create(
                user=user,
                branch=branch,
                defaults={"has_all_branch_access": True, "is_default": True},
            )
            UserSecurityProfile.objects.update_or_create(
                user=user,
                defaults={
                    "must_change_password": False,
                    "is_internal_sentinel_staff": account["is_internal"],
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Synthetic staging demo organizations and users are ready."
            )
        )
