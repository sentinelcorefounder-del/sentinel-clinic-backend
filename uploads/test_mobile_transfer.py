import hashlib
import io
import tempfile
from datetime import date, timedelta
from unittest.mock import patch

from PIL import Image

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from encounters.models import ScreeningEncounter
from organizations.models import Organization, OrganizationBranch
from patients.models import Patient
from users.models import UserBranchAccess, UserOrganization

from .models import MobileTransferSession, PendingMobileImage


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(output, format="JPEG")
    return output.getvalue()


class MobileTransferAccessTests(TestCase):
    def setUp(self):
        self.staging_temp = tempfile.TemporaryDirectory()
        self.clinical_temp = tempfile.TemporaryDirectory()
        self.storage_override = override_settings(
            BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE=False,
            BULK_STAGING_ROOT=self.staging_temp.name,
            CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE=False,
            PRIVATE_CLINICAL_ASSETS_ROOT=self.clinical_temp.name,
        )
        self.storage_override.enable()
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
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Main",
            is_head_office=True,
        )
        self.patient = Patient.objects.create(
            patient_id="PAT-TRANSFER",
            first_name="Test",
            last_name="Patient",
            date_of_birth=date(1980, 1, 1),
            sex="female",
            assigned_clinic=self.clinic,
            assigned_branch=self.branch,
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-TRANSFER",
            patient=self.patient,
            encounter_date=date.today(),
            programme="ocular_diagnostics",
            originating_organization=self.clinic,
            service_branch=self.branch,
        )
        self.clinic_user = User.objects.create_user("clinic-transfer-user")
        UserOrganization.objects.create(user=self.clinic_user, organization=self.clinic)
        UserBranchAccess.objects.create(user=self.clinic_user, branch=self.branch, is_default=True)
        self.other_user = User.objects.create_user("other-transfer-user")
        UserOrganization.objects.create(user=self.other_user, organization=self.other_clinic)
        self.client = APIClient()

    def tearDown(self):
        self.storage_override.disable()
        self.staging_temp.cleanup()
        self.clinical_temp.cleanup()

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

    @patch("uploads.views.run_ai_analysis")
    def test_public_pending_upload_is_staged_privately_and_confirmation_is_idempotent(self, run_ai_analysis):
        token = "synthetic-mobile-token"
        session = MobileTransferSession.objects.create(
            encounter=self.encounter, initiated_by=self.clinic_user,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        response = self.client.post(
            reverse("mobile-transfer-public", args=[token]),
            {"images": [SimpleUploadedFile("phone.jpg", image_bytes(), content_type="image/jpeg")]},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        item = session.pending_images.get()
        self.assertTrue(item.staged_object_key)
        self.assertEqual(item.image_file.name, "")
        self.assertNotIn(item.staged_object_key, str(response.data))

        self.client.force_authenticate(self.clinic_user)
        url = reverse("mobile-transfer-image-review", args=[session.session_id, item.id])
        payload = {"action": "confirm", "eye_laterality": "left", "image_quality": "good"}
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 200)
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, "confirmed")
        self.assertEqual(item.staged_object_key, "")
        self.assertEqual(self.encounter.image_uploads.count(), 1)
        self.assertEqual(item.confirmed_upload.storage_kind, "private_clinical")
        run_ai_analysis.assert_not_called()

    def test_expired_session_removes_unconfirmed_staged_object(self):
        token = "expired-mobile-token"
        session = MobileTransferSession.objects.create(
            encounter=self.encounter, initiated_by=self.clinic_user,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        response = self.client.post(
            reverse("mobile-transfer-public", args=[token]),
            {"images": [SimpleUploadedFile("phone.jpg", image_bytes(), content_type="image/jpeg")]},
            format="multipart",
        )
        item = session.pending_images.get()
        key = item.staged_object_key
        session.expires_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["expires_at"])
        self.assertEqual(self.client.get(reverse("mobile-transfer-public", args=[token])).status_code, 400)
        self.assertFalse(PendingMobileImage.objects.filter(pk=item.pk).exists())
        from uploads.storage import get_bulk_staging_storage
        self.assertFalse(get_bulk_staging_storage().exists(key))
