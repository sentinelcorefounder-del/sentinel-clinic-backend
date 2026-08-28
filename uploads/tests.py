import io
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from encounters.models import ScreeningEncounter
from organizations.models import Organization, OrganizationBranch
from patients.models import Patient
from users.models import UserBranchAccess, UserOrganization

from .models import ImageUpload


def valid_image():
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(output, format="JPEG")
    return output.getvalue()


class PrivateNormalUploadTests(TestCase):
    def setUp(self):
        self.private_temp = tempfile.TemporaryDirectory()
        self.media_temp = tempfile.TemporaryDirectory()
        self.storage_override = override_settings(
            CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE=False,
            PRIVATE_CLINICAL_ASSETS_ROOT=self.private_temp.name,
            MEDIA_ROOT=self.media_temp.name,
        )
        self.storage_override.enable()
        self.clinic = Organization.objects.create(
            clinic_id="UPLOAD-CLINIC", name="Upload Clinic", organization_type="clinic"
        )
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Main"
        )
        self.patient = Patient.objects.create(
            patient_id="UPLOAD-PAT", first_name="Synthetic", last_name="Patient",
            date_of_birth=date(1980, 1, 1), sex="female",
            assigned_clinic=self.clinic, assigned_branch=self.branch,
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="UPLOAD-ENC", patient=self.patient,
            encounter_date=date(2026, 8, 15), originating_organization=self.clinic,
            service_branch=self.branch,
        )
        self.user = User.objects.create_user("upload-user")
        self.user.groups.add(
            Group.objects.create(name="clinic_screener"),
            Group.objects.create(name="optometrist"),
        )
        UserOrganization.objects.create(user=self.user, organization=self.clinic)
        UserBranchAccess.objects.create(user=self.user, branch=self.branch)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def tearDown(self):
        self.storage_override.disable()
        self.private_temp.cleanup()
        self.media_temp.cleanup()

    def test_new_normal_upload_is_private_and_returns_only_application_url(self):
        response = self.client.post(
            reverse("image-upload-list-create"),
            {
                "image_upload_id": "NORMAL-PRIVATE",
                "encounter": self.encounter.pk,
                "patient": self.patient.pk,
                "eye_laterality": "left",
                "image_type": "fundus",
                "image_file": SimpleUploadedFile(
                    "patient-supplied-name.jpg", valid_image(), content_type="image/jpeg"
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        upload = ImageUpload.objects.get(pk=response.data["id"])
        self.assertEqual(upload.storage_kind, "private_clinical")
        self.assertEqual(upload.image_file.name, "")
        self.assertNotIn("patient-supplied-name", upload.private_object_key)
        self.assertIn(f"/api/uploads/{upload.pk}/content/", response.data["image_file"])
        self.assertNotIn(upload.private_object_key, str(response.data))
        from uploads.ai_services import image_file_to_data_url
        self.assertTrue(image_file_to_data_url(upload).startswith("data:image/jpeg;base64,"))
        content = self.client.get(reverse("image-upload-content", args=[upload.pk]))
        self.assertEqual(content.status_code, 200)
        content.close()

    def test_legacy_default_image_uses_same_protected_endpoint(self):
        upload = ImageUpload.objects.create(
            image_upload_id="LEGACY-UPLOAD", encounter=self.encounter,
            patient=self.patient, eye_laterality="right",
            image_file=SimpleUploadedFile("legacy.jpg", valid_image(), content_type="image/jpeg"),
        )
        response = self.client.get(reverse("image-upload-content", args=[upload.pk]))
        self.assertEqual(response.status_code, 200)
        response.close()

    @patch("uploads.models.ImageUpload.save", side_effect=RuntimeError("database failure"))
    def test_database_failure_removes_prepared_private_object(self, mocked_save):
        self.client.raise_request_exception = False
        response = self.client.post(
            reverse("image-upload-list-create"),
            {
                "image_upload_id": "FAILED-PRIVATE",
                "encounter": self.encounter.pk,
                "patient": self.patient.pk,
                "eye_laterality": "left",
                "image_type": "fundus",
                "image_file": SimpleUploadedFile(
                    "failed.jpg", valid_image(), content_type="image/jpeg"
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 500)
        self.assertFalse(ImageUpload.objects.exists())
        self.assertEqual(
            [path for path in Path(self.private_temp.name).rglob("*") if path.is_file()],
            [],
        )

# Create your tests here.
