from django.contrib.auth.models import Group, User
from django.test import override_settings
from rest_framework.test import APITestCase

from organizations.models import Organization, OrganizationBranch
from users.models import ClinicalProfessionalProfile, UserBranchAccess, UserOrganization, UserSecurityProfile


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", FRONTEND_URL="http://testserver")
class OrganizationStaffManagementTests(APITestCase):
    def setUp(self):
        self.clinic = Organization.objects.create(clinic_id="CL-STAFF", name="Staff Clinic", organization_type="clinic")
        self.other_clinic = Organization.objects.create(clinic_id="CL-OTHER", name="Other Clinic", organization_type="clinic")
        self.main = OrganizationBranch.objects.create(organization=self.clinic, branch_code="MAIN", name="Main", is_head_office=True)
        self.branch = OrganizationBranch.objects.create(organization=self.clinic, branch_code="B2", name="Branch Two")
        self.other_branch = OrganizationBranch.objects.create(organization=self.other_clinic, branch_code="MAIN", name="Other Main")

        self.admin = User.objects.create_user(username="clinic-admin", password="pass12345")
        self.admin.groups.add(Group.objects.get_or_create(name="clinic_admin")[0])
        UserOrganization.objects.create(user=self.admin, organization=self.clinic)
        UserBranchAccess.objects.create(user=self.admin, branch=self.main, has_all_branch_access=True, is_default=True)

    def test_clinic_admin_can_create_staff_with_clinical_role_and_branch_scope(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/organizations/{self.clinic.id}/staff/",
            {
                "username": "new-opto",
                "email": "new-opto@example.test",
                "first_name": "New",
                "last_name": "Optometrist",
                "roles": ["optometrist"],
                "all_branch_access": False,
                "branch_ids": [self.branch.id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(username="new-opto")
        self.assertTrue(user.groups.filter(name="optometrist").exists())
        self.assertEqual(user.organization_link.organization_id, self.clinic.id)
        self.assertEqual(list(UserBranchAccess.objects.filter(user=user).values_list("branch_id", flat=True)), [self.branch.id])
        self.assertFalse(hasattr(user, "clinical_professional_profile"))

    def test_clinic_admin_cannot_manage_another_clinic(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/organizations/{self.other_clinic.id}/staff/")
        self.assertEqual(response.status_code, 403)

    def test_cross_clinic_branch_assignment_is_rejected(self):
        staff = User.objects.create_user(username="staff", password="pass12345")
        UserOrganization.objects.create(user=staff, organization=self.clinic)
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/organizations/{self.clinic.id}/staff/{staff.id}/",
            {"all_branch_access": False, "branch_ids": [self.other_branch.id]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_clinic_admin_cannot_assign_internal_or_owner_role(self):
        staff = User.objects.create_user(username="staff-two", password="pass12345")
        UserOrganization.objects.create(user=staff, organization=self.clinic)
        self.client.force_authenticate(self.admin)
        for roles in (["ops_admin"], ["finance_admin"], ["clinic_owner_optometrist"]):
            response = self.client.patch(
                f"/api/organizations/{self.clinic.id}/staff/{staff.id}/",
                {"roles": roles}, format="json",
            )
            self.assertEqual(response.status_code, 400)

    def test_profile_verification_is_ops_only_and_does_not_create_clinical_role(self):
        staff = User.objects.create_user(username="profile-staff", password="pass12345")
        UserOrganization.objects.create(user=staff, organization=self.clinic)
        ClinicalProfessionalProfile.objects.create(
            user=staff,
            display_name="Dr Profile",
            professional_role="Optometrist",
            registration_number="TEST-1",
        )
        self.client.force_authenticate(self.admin)
        denied = self.client.patch(
            f"/api/organizations/{self.clinic.id}/staff/{staff.id}/",
            {"clinical_profile_verified": True}, format="json",
        )
        self.assertEqual(denied.status_code, 403)

        ops = User.objects.create_user(username="ops", password="pass12345")
        ops.groups.add(Group.objects.get_or_create(name="ops_admin")[0])
        UserSecurityProfile.objects.create(user=ops, is_internal_sentinel_staff=True)
        self.client.force_authenticate(ops)
        allowed = self.client.patch(
            f"/api/organizations/{self.clinic.id}/staff/{staff.id}/",
            {"clinical_profile_verified": True}, format="json",
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        staff.refresh_from_db()
        self.assertTrue(staff.clinical_professional_profile.is_verified)
        self.assertFalse(staff.groups.filter(name__in=["optometrist", "reviewer", "clinic_owner_optometrist"]).exists())
