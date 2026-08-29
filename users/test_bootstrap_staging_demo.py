import os
import secrets
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings

from organizations.models import Organization, OrganizationBranch
from users.models import UserBranchAccess, UserOrganization, UserSecurityProfile


PASSWORDS = {
    name: secrets.token_urlsafe(32)
    for name in (
        "SENTINEL_STAGING_OPS_PASSWORD",
        "SENTINEL_STAGING_CLINIC_PASSWORD",
        "SENTINEL_STAGING_REVIEWER_PASSWORD",
        "SENTINEL_STAGING_HOSPITAL_PASSWORD",
    )
}


@override_settings(
    ALLOWED_HOSTS=["sentinel-clinic-backend-staging.onrender.com"],
    FRONTEND_URL="https://sentinel-clinic-frontend-staging.example.invalid",
)
class BootstrapStagingDemoTests(TestCase):
    def environment(self):
        return {
            "SENTINEL_STAGING_BOOTSTRAP_ENABLED": "true",
            "SENTINEL_STAGING_DATABASE_NAME": "sentinel-clinic-db-staging",
            **PASSWORDS,
        }

    def run_command(self, environment=None):
        output = StringIO()
        with (
            patch.dict(os.environ, environment or self.environment(), clear=True),
            patch.dict(
                connection.settings_dict,
                {"NAME": "sentinel-clinic-db-staging"},
                clear=False,
            ),
        ):
            call_command("bootstrap_staging_demo", stdout=output)
        return output.getvalue()

    def test_requires_explicit_staging_flag(self):
        environment = self.environment()
        environment.pop("SENTINEL_STAGING_BOOTSTRAP_ENABLED")

        with self.assertRaisesMessage(CommandError, "not explicitly enabled"):
            self.run_command(environment)

    def test_refuses_production_host(self):
        with override_settings(ALLOWED_HOSTS=["api.usesentinelhealth.com"]):
            with self.assertRaisesMessage(CommandError, "Production hosts"):
                self.run_command()

    def test_refuses_database_name_mismatch(self):
        environment = self.environment()
        environment["SENTINEL_STAGING_DATABASE_NAME"] = "another-staging-database"

        with self.assertRaisesMessage(CommandError, "explicitly named staging database"):
            self.run_command(environment)

    def test_creates_exact_synthetic_accounts_and_is_idempotent(self):
        first_output = self.run_command()
        second_output = self.run_command()

        User = get_user_model()
        self.assertEqual(User.objects.filter(username__startswith="staging_demo_").count(), 4)
        self.assertEqual(Organization.objects.filter(clinic_id__startswith="STG-").count(), 3)
        self.assertEqual(OrganizationBranch.objects.filter(organization__clinic_id__startswith="STG-").count(), 3)
        self.assertEqual(UserOrganization.objects.filter(user__username__startswith="staging_demo_").count(), 4)
        self.assertEqual(UserBranchAccess.objects.filter(user__username__startswith="staging_demo_").count(), 4)

        expected_roles = {
            "staging_demo_ops": {"sentinel_ops"},
            "staging_demo_clinic": {"clinic_admin", "optometrist"},
            "staging_demo_reviewer": {"reviewer"},
            "staging_demo_hospital": {"hospital_admin"},
        }
        for username, roles in expected_roles.items():
            user = User.objects.get(username=username)
            self.assertEqual(set(user.groups.values_list("name", flat=True)), roles)
            self.assertFalse(user.is_superuser)
            self.assertTrue(user.check_password(PASSWORDS[f"SENTINEL_STAGING_{username.removeprefix('staging_demo_').upper()}_PASSWORD"]))

        ops = User.objects.get(username="staging_demo_ops")
        self.assertTrue(ops.is_staff)
        self.assertTrue(UserSecurityProfile.objects.get(user=ops).is_internal_sentinel_staff)
        self.assertFalse(User.objects.exclude(pk=ops.pk).filter(is_staff=True).exists())

        combined = User.objects.get(username="staging_demo_clinic")
        self.assertEqual(combined.organization_link.organization.clinic_id, "STG-CLINIC-DEMO")
        self.assertTrue(combined.branch_access.get().has_all_branch_access)
        self.assertTrue(combined.branch_access.get().is_default)

        for password in PASSWORDS.values():
            self.assertNotIn(password, first_output)
            self.assertNotIn(password, second_output)

        self.assertEqual(
            Organization.objects.filter(clinic_id__startswith="STG-").count(),
            3,
        )
