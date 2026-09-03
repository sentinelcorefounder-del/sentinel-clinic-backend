from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.test import APIClient
from organizations.models import Organization
from users.models import UserOrganization


class CurrentUserExplicitMembershipTests(TestCase):
    def test_superuser_explicit_clinic_membership_is_represented_without_granting_role(self):
        clinic = Organization.objects.create(clinic_id="AUTH-CLINIC", name="Auth Clinic", organization_type="clinic")
        user = User.objects.create_user("auth-super-opto", password="test", is_superuser=True)
        user.groups.add(Group.objects.create(name="optometrist"))
        UserOrganization.objects.create(user=user, organization=clinic)
        client = APIClient(); client.force_authenticate(user)
        response = client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["organization"]["id"], clinic.pk)
        self.assertIn("optometrist", response.data["roles"])

    def test_superuser_without_explicit_membership_has_no_organization(self):
        user = User.objects.create_user("auth-super-only", password="test", is_superuser=True)
        client = APIClient(); client.force_authenticate(user)
        response = client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["organization"])
