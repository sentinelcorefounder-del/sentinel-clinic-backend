from django.contrib.auth.models import Group, User
from rest_framework.test import APITestCase

from users.models import ClinicalProfessionalProfile


class ClinicalProfessionalProfileTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="clinician-profile-test", password="pass12345")
        Group.objects.get_or_create(name="clinic_admin")[0].user_set.add(self.user)
        self.client.force_authenticate(self.user)

    def test_profile_completion_does_not_grant_clinical_authority(self):
        response = self.client.patch(
            "/api/auth/clinical-profile/",
            {
                "display_name": "Test Clinician",
                "professional_role": "Optometrist",
                "registration_number": "TEST-001",
                "registration_body": "GOC",
                "qualifications": "BSc Optom",
                "signature_name": "Test Clinician",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.user.groups.filter(name__in=["optometrist", "reviewer", "clinic_owner_optometrist"]).exists())

    def test_material_edit_resets_verification(self):
        profile = ClinicalProfessionalProfile.objects.create(
            user=self.user, display_name="Test Clinician", professional_role="Optometrist",
            registration_number="TEST-001", registration_body="GOC",
            qualifications="BSc Optom", signature_name="Test Clinician", is_verified=True,
        )
        response = self.client.patch(
            "/api/auth/clinical-profile/", {"registration_number": "TEST-002"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertFalse(profile.is_verified)
        self.assertIsNone(profile.verified_at)
