from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.test import APIClient

from organizations.models import Organization, OrganizationBranch
from users.models import UserBranchAccess, UserOrganization


class OrganizationBranchTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            clinic_id="CHAIN-1",
            name="Chain Eye Clinic",
            organization_type="clinic",
        )
        self.main = OrganizationBranch.objects.create(
            organization=self.organization,
            branch_code="MAIN",
            name="Main Branch",
            is_head_office=True,
        )
        self.user = User.objects.create_user("group-admin", password="test-password")
        self.user.groups.add(Group.objects.create(name="clinic_admin"))
        UserOrganization.objects.create(user=self.user, organization=self.organization)
        UserBranchAccess.objects.create(
            user=self.user,
            branch=self.main,
            has_all_branch_access=True,
            is_default=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_admin_can_create_branch_under_own_organization(self):
        response = self.client.post(
            f"/api/organizations/{self.organization.id}/branches/",
            {"branch_code": "ikeja", "name": "Ikeja Branch"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            self.organization.branches.filter(
                branch_code="IKEJA",
                name="Ikeja Branch",
            ).exists()
        )

    def test_branch_code_is_unique_within_parent(self):
        OrganizationBranch.objects.create(
            organization=self.organization,
            branch_code="IKEJA",
            name="Ikeja Branch",
        )
        response = self.client.post(
            f"/api/organizations/{self.organization.id}/branches/",
            {"branch_code": "ikeja", "name": "Another Branch"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("branch_code", response.data)

    def test_user_cannot_manage_another_organization_branches(self):
        other = Organization.objects.create(
            clinic_id="OTHER-1",
            name="Other Clinic",
            organization_type="clinic",
        )
        response = self.client.post(
            f"/api/organizations/{other.id}/branches/",
            {"branch_code": "NEW", "name": "New Branch"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
