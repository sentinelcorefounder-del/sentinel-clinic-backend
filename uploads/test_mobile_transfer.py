import hashlib
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from encounters.models import ScreeningEncounter
from organizations.models import Organization
from patients.models import Patient
from users.models import UserOrganization

from .models import MobileTransferSession, PendingMobileImage


class MobileTransferAccessTests(TestCase):
    def setUp(self):
        self.clinic = Organization.objects.create(
            clinic_id="CLINIC-TRANSFER",
            name="Transfer Clinic",
            organization_type="clinic",
        )
        self.other_clinic = Organization.objects.create(
            clinic_id="OTHER-CLINIC",
            name="Other Clinic",
            organization_type="clinic",
        )
        self.patient = Patient.objects.create(
            patient_id="PAT-TRANSFER",
            first_name="Test",
            last_name="Patient",
            date_of_birth=date(1980, 1, 1),
            sex="female",
            assigned_clinic=self.clinic,
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-TRANSFER",
            patient=self.patient,
            encounter_date=date.today(),
            programme="ocular_diagnostics",
        )
        self.clinic_user = User.objects.create_user("clinic-transfer-user")
        UserOrganization.objects.create(user=self.clinic_user, organization=self.clinic)
        self.other_user = User.objects.create_user("other-transfer-user")
        UserOrganization.objects.create(user=self.other_user, organization=self.other_clinic)
        self.client = APIClient()

    def test_linked_clinic_user_can_start_transfer_without_special_upload_group(self):
        self.client.force_authenticate(self.clinic_user)

        response = self.client.post(
            reverse("mobile-transfer-create", args=[self.encounter.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 201)

    def test_user_from_another_clinic_cannot_start_transfer(self):
        self.client.force_authenticate(self.other_user)

        response = self.client.post(
            reverse("mobile-transfer-create", args=[self.encounter.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    @patch("uploads.views.run_ai_analysis")
    def test_review_confirms_pending_image_for_nullable_clinic_relation(self, run_ai_analysis):
        session = MobileTransferSession.objects.create(
            encounter=self.encounter,
            initiated_by=self.clinic_user,
            token_hash="a" * 64,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        image_bytes = b"test-image-content"
        item = PendingMobileImage.objects.create(
            session=session,
            image_file=SimpleUploadedFile("left.jpg", image_bytes, content_type="image/jpeg"),
            original_filename="left.jpg",
            checksum_sha256=hashlib.sha256(image_bytes).hexdigest(),
        )
        self.client.force_authenticate(self.clinic_user)

        response = self.client.post(
            reverse("mobile-transfer-image-review", args=[session.session_id, item.id]),
            {
                "action": "confirm",
                "eye_laterality": "left",
                "image_quality": "good",
                "gradable": True,
                "retake_required": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, "confirmed")
        self.assertIsNotNone(item.confirmed_upload_id)
        run_ai_analysis.assert_not_called()
