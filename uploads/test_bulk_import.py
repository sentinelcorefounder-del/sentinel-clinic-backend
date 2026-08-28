import io
import os
import tempfile
import zipfile
from datetime import date
from unittest.mock import patch

from PIL import Image
from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from encounters.models import AssessmentServiceSession, ScreeningEncounter
from organizations.models import Organization, OrganizationBranch
from patients.identity_services import ensure_master_identity
from patients.models import Patient
from referrals.models import HospitalReferral
from reports.models import StructuredReport
from users.models import UserBranchAccess, UserOrganization, UserSecurityProfile
from uploads.bulk_import import parse_remidio_root
from uploads.bulk_import import _safe_member, _validate_archive_infos, _validate_image
from uploads.models import BulkImageAttachment, BulkImageImport, BulkImageImportItem, ImageUpload
from uploads.storage import BulkStagingConfigurationError, get_bulk_staging_storage
from uploads.checks import (
    private_bulk_staging_check,
    private_clinical_assets_check,
    private_storage_separation_check,
)
from uploads.clinical_assets import get_private_clinical_storage
from uploads.clinical_assets import save_private_copy as real_save_private_copy
from uploads.bulk_import import cleanup_import, cleanup_uncommitted_private_assets


def image_bytes(fmt="JPEG"):
    image = Image.effect_noise((96, 96), 80).convert("RGB")
    output = io.BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


def remidio_archive(root="Synthetic_Patient_0047_15-08-2026", extra=None):
    inner_bytes = io.BytesIO()
    with zipfile.ZipFile(inner_bytes, "w", zipfile.ZIP_DEFLATED) as inner:
        inner.writestr(f"{root}/Images/1.00.00.001_image.jpg", image_bytes())
        inner.writestr(f"{root}/Images/2.00.00.001_image.jpg", image_bytes())
        if extra:
            for name, content in extra:
                inner.writestr(name, content)
    outer_bytes = io.BytesIO()
    with zipfile.ZipFile(outer_bytes, "w", zipfile.ZIP_STORED) as outer:
        outer.writestr("Fundus/0001_15-08-2026_1.00.00.001_export.zip", inner_bytes.getvalue())
    return outer_bytes.getvalue()


class RemidioParserTests(TestCase):
    def test_variable_length_mrn_and_leading_zero_are_preserved(self):
        for mrn in ("0047", "10452", "AB-000047"):
            parsed, parsed_date = parse_remidio_root(f"Synthetic_Patient_{mrn}_15-08-2026")
            self.assertEqual(parsed, mrn)
            self.assertEqual(parsed_date, date(2026, 8, 15))

    def test_only_valid_pre_date_boundary_is_accepted(self):
        self.assertEqual(parse_remidio_root("Synthetic_0047_31-02-2026"), (None, None))
        self.assertEqual(parse_remidio_root("Synthetic_0047_15-08-26"), (None, None))

    def test_unrelated_numeric_sequences_are_not_selected(self):
        mrn, _ = parse_remidio_root("Synthetic_2024_Device99_AB-10452_15-08-2026")
        self.assertEqual(mrn, "AB-10452")


class ArchiveSafetyUnitTests(TestCase):
    def test_path_traversal_drive_paths_symlinks_and_special_files_are_unsafe(self):
        for name in ("../escape.jpg", "folder/../../escape.jpg", "C:/escape.jpg", "/escape.jpg"):
            self.assertFalse(_safe_member(zipfile.ZipInfo(name)))
        symlink = zipfile.ZipInfo("image.jpg")
        symlink.external_attr = (0o120777 << 16)
        self.assertFalse(_safe_member(symlink))
        device = zipfile.ZipInfo("image.jpg")
        device.external_attr = (0o060600 << 16)
        self.assertFalse(_safe_member(device))

    def test_encrypted_and_excessive_ratio_entries_are_rejected(self):
        encrypted = zipfile.ZipInfo("image.jpg")
        encrypted.flag_bits = 0x1
        with self.assertRaises(Exception):
            _validate_archive_infos([encrypted])
        ratio = zipfile.ZipInfo("image.jpg")
        ratio.file_size = 1000
        ratio.compress_size = 1
        with self.assertRaises(Exception):
            _validate_archive_infos([ratio])

    def test_image_magic_extension_and_corruption_are_enforced(self):
        with self.assertRaises(Exception):
            _validate_image(image_bytes("JPEG"), ".png")
        with self.assertRaises(Exception):
            _validate_image(b"\xff\xd8\xffbroken", ".jpg")

    @override_settings(BULK_IMPORT_MAX_IMAGE_WIDTH=10)
    def test_image_dimension_limit_is_enforced(self):
        with self.assertRaises(Exception):
            _validate_image(image_bytes(), ".jpg")


class PrivateStorageFailureTests(TestCase):
    def private_settings(self, **overrides):
        values = {
            "BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE": True,
            "CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE": True,
            "BULK_STAGING_REQUIRED_SETTINGS": ("BULK_STAGING_R2_ACCOUNT_ID", "BULK_STAGING_R2_ACCESS_KEY_ID", "BULK_STAGING_R2_SECRET_ACCESS_KEY", "BULK_STAGING_R2_BUCKET_NAME"),
            "CLINICAL_ASSETS_REQUIRED_SETTINGS": ("CLINICAL_ASSETS_R2_ACCOUNT_ID", "CLINICAL_ASSETS_R2_ACCESS_KEY_ID", "CLINICAL_ASSETS_R2_SECRET_ACCESS_KEY", "CLINICAL_ASSETS_R2_BUCKET_NAME"),
            "R2_BUCKET_NAME": "ordinary-media",
            "BULK_STAGING_R2_ACCOUNT_ID": "shared-account",
            "BULK_STAGING_R2_ACCESS_KEY_ID": "staging-access-key",
            "BULK_STAGING_R2_SECRET_ACCESS_KEY": "staging-secret",
            "BULK_STAGING_R2_BUCKET_NAME": "sentinel-bulk-staging",
            "CLINICAL_ASSETS_R2_ACCOUNT_ID": "shared-account",
            "CLINICAL_ASSETS_R2_ACCESS_KEY_ID": "clinical-access-key",
            "CLINICAL_ASSETS_R2_SECRET_ACCESS_KEY": "clinical-secret",
            "CLINICAL_ASSETS_R2_BUCKET_NAME": "sentinel-clinical-assets",
            "STORAGES": {
                "default": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": {"bucket_name": "ordinary-media", "querystring_auth": False}},
                "bulk_staging": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": {"access_key": "staging-access-key", "bucket_name": "sentinel-bulk-staging", "default_acl": "private", "querystring_auth": True, "custom_domain": None}},
                "private_clinical_assets": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": {"access_key": "clinical-access-key", "bucket_name": "sentinel-clinical-assets", "default_acl": "private", "querystring_auth": True, "custom_domain": None}},
            },
        }
        values.update(overrides)
        return values

    def test_missing_production_configuration_fails_closed(self):
        with override_settings(
            BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE=True,
            BULK_STAGING_REQUIRED_SETTINGS=("PRIVATE_TEST_VALUE",),
            PRIVATE_TEST_VALUE="",
        ):
            with self.assertRaises(BulkStagingConfigurationError):
                get_bulk_staging_storage()
            self.assertEqual(private_bulk_staging_check(None)[0].id, "uploads.E001")

    def test_synthetic_private_s3_configuration_uses_signed_access_without_domain(self):
        names = ("A", "K", "S", "B")
        with override_settings(
            BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE=True,
            BULK_STAGING_REQUIRED_SETTINGS=names,
            A="account", K="access", S="secret", B="bucket",
            STORAGES={
                "bulk_staging": {
                    "BACKEND": "storages.backends.s3.S3Storage",
                    "OPTIONS": {
                        "access_key": "access", "secret_key": "secret",
                        "bucket_name": "bucket", "endpoint_url": "https://synthetic.invalid",
                        "region_name": "auto", "default_acl": "private",
                        "querystring_auth": True, "custom_domain": None,
                    },
                }
            },
        ):
            storage = get_bulk_staging_storage()
            self.assertTrue(storage.querystring_auth)
            self.assertIsNone(storage.custom_domain)

    def test_valid_separate_private_storage_configuration_passes(self):
        with override_settings(**self.private_settings()):
            self.assertEqual(private_bulk_staging_check(None), [])
            self.assertEqual(private_clinical_assets_check(None), [])
            self.assertEqual(private_storage_separation_check(None), [])

    @override_settings(
        BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE=False,
        CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE=False,
    )
    def test_local_file_storage_configuration_remains_usable(self):
        self.assertEqual(private_bulk_staging_check(None), [])
        self.assertEqual(private_clinical_assets_check(None), [])
        self.assertEqual(private_storage_separation_check(None), [])

    def test_private_buckets_must_be_distinct(self):
        with override_settings(**self.private_settings(
            CLINICAL_ASSETS_R2_BUCKET_NAME="sentinel-bulk-staging",
        )):
            self.assertEqual(private_storage_separation_check(None)[0].id, "uploads.E005")

    def test_private_buckets_cannot_reuse_default_media_bucket(self):
        for setting_name, error_id in (
            ("BULK_STAGING_R2_BUCKET_NAME", "uploads.E006"),
            ("CLINICAL_ASSETS_R2_BUCKET_NAME", "uploads.E007"),
        ):
            with self.subTest(setting_name=setting_name), override_settings(
                **self.private_settings(**{setting_name: "ordinary-media"})
            ):
                self.assertEqual(private_storage_separation_check(None)[0].id, error_id)

    def test_private_storages_must_use_separate_access_keys(self):
        with override_settings(**self.private_settings(
            CLINICAL_ASSETS_R2_ACCESS_KEY_ID="staging-access-key",
        )):
            self.assertEqual(private_storage_separation_check(None)[0].id, "uploads.E008")

    def test_unsigned_or_custom_domain_private_aliases_fail_closed(self):
        for alias, check in (
            ("bulk_staging", private_bulk_staging_check),
            ("private_clinical_assets", private_clinical_assets_check),
        ):
            for unsafe_options in (
                {"default_acl": "private", "querystring_auth": False, "custom_domain": None},
                {"default_acl": "private", "querystring_auth": True, "custom_domain": "public.invalid"},
                {"default_acl": None, "querystring_auth": True, "custom_domain": None},
            ):
                settings_values = self.private_settings()
                settings_values["STORAGES"] = dict(settings_values["STORAGES"])
                settings_values["STORAGES"][alias] = {
                    "BACKEND": "storages.backends.s3.S3Storage",
                    "OPTIONS": unsafe_options,
                }
                with self.subTest(alias=alias, options=unsafe_options), override_settings(**settings_values):
                    self.assertTrue(check(None))


class PrivateClinicalAssetAuthorizationTests(TestCase):
    def setUp(self):
        self.clinical_temp = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE=False,
            PRIVATE_CLINICAL_ASSETS_ROOT=self.clinical_temp.name,
        )
        self.settings_override.enable()
        self.clinic = Organization.objects.create(clinic_id="AUTH-CLINIC", name="Authorized Clinic", organization_type="clinic")
        self.other_clinic = Organization.objects.create(clinic_id="OTHER-CLINIC", name="Other Clinic", organization_type="clinic")
        self.hospital = Organization.objects.create(clinic_id="AUTH-HOSP", name="Authorized Hospital", organization_type="hospital")
        self.other_hospital = Organization.objects.create(clinic_id="OTHER-HOSP", name="Other Hospital", organization_type="hospital")
        self.branch = OrganizationBranch.objects.create(organization=self.clinic, branch_code="MAIN", name="Main")
        self.other_branch = OrganizationBranch.objects.create(organization=self.clinic, branch_code="OTHER", name="Other")
        self.patient = Patient.objects.create(patient_id="AUTH-PAT", first_name="Synthetic", last_name="Patient", date_of_birth=date(1980, 1, 1), sex="female", assigned_clinic=self.clinic, assigned_branch=self.branch)
        self.referral = HospitalReferral.objects.create(source_hospital=self.hospital, patient=self.patient, first_name="Synthetic", last_name="Patient", hospital_mrn="AUTH-001", reason_for_referral="Synthetic", matched_clinic=self.clinic, matched_branch=self.branch)
        self.encounter = ScreeningEncounter.objects.create(encounter_id="AUTH-ENC", patient=self.patient, encounter_date=date(2026, 8, 15), originating_organization=self.clinic, service_branch=self.branch, hospital_referral=self.referral)
        self.object_key = "clinical-assets/synthetic/authorization-image.jpg"
        get_private_clinical_storage().save(self.object_key, ContentFile(image_bytes()))
        self.upload = ImageUpload.objects.create(
            image_upload_id="AUTH-UPLOAD", encounter=self.encounter, patient=self.patient,
            eye_laterality="left", image_file="", storage_kind="private_clinical",
            private_object_key=self.object_key, content_sha256="a" * 64,
            source_format="JPEG", asset_organization=self.clinic, asset_branch=self.branch,
        )
        self.url = reverse("image-upload-content", args=[self.upload.pk])
        self.client = APIClient()

    def tearDown(self):
        self.settings_override.disable()
        self.clinical_temp.cleanup()

    def user(self, username, roles=(), organization=None, branch=None, internal=False, superuser=False):
        user = User.objects.create_user(username, is_superuser=superuser, is_staff=superuser)
        for role in roles:
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        if organization:
            UserOrganization.objects.create(user=user, organization=organization)
        if branch:
            UserBranchAccess.objects.create(user=user, branch=branch)
        if internal:
            UserSecurityProfile.objects.create(user=user, is_internal_sentinel_staff=True)
        return user

    def status_for(self, user=None):
        self.client.force_authenticate(user=user)
        response = self.client.get(self.url)
        if response.status_code == 200:
            response.close()
        return response.status_code

    def test_unauthenticated_access_is_denied(self):
        self.assertIn(self.status_for(), (401, 403))

    def test_same_clinic_clinician_with_correct_branch_is_allowed(self):
        user = self.user("clinic-optometrist", ("optometrist",), self.clinic, self.branch)
        self.client.force_authenticate(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.object_key, " ".join(response.headers.values()))
        response.close()

    def test_same_clinic_clinician_with_wrong_branch_is_denied(self):
        user = self.user("wrong-branch", ("optometrist", "ops_admin"), self.clinic, self.other_branch)
        self.assertEqual(self.status_for(user), 403)

    def test_other_clinic_clinical_roles_are_denied(self):
        other_branch = OrganizationBranch.objects.create(organization=self.other_clinic, branch_code="MAIN", name="Main")
        for role in ("optometrist", "reviewer"):
            with self.subTest(role=role):
                user = self.user(f"other-{role}", (role,), self.other_clinic, other_branch)
                self.assertEqual(self.status_for(user), 403)

    def test_non_clinical_clinic_role_is_denied(self):
        user = self.user("clinic-admin", ("clinic_admin",), self.clinic, self.branch)
        self.assertEqual(self.status_for(user), 403)

    def test_internal_access_requires_marker_and_exact_role(self):
        role_only = self.user("role-only", ("ops_admin",))
        marker_only = self.user("marker-only", internal=True)
        authorized = self.user("authorized-ops", ("sentinel_ops",), internal=True)
        self.assertEqual(self.status_for(role_only), 403)
        self.assertEqual(self.status_for(marker_only), 403)
        self.assertEqual(self.status_for(authorized), 200)

    def test_superuser_status_alone_is_denied(self):
        self.assertEqual(self.status_for(self.user("super-only", superuser=True)), 403)

    def test_hospital_access_requires_exact_hospital_and_canonical_release(self):
        hospital_user = self.user("hospital-user", ("hospital_admin",), self.hospital)
        other_hospital_user = self.user("other-hospital-user", ("hospital_admin",), self.other_hospital)
        self.assertEqual(self.status_for(hospital_user), 403)
        report = StructuredReport.objects.create(
            report_id="AUTH-REPORT", encounter=self.encounter, patient=self.patient,
            review_date=date(2026, 8, 15), report_status="issued",
            distribution_status="released_to_hospital", hospital_released_at=timezone.now(),
        )
        self.referral.report = report
        self.referral.report_ready = True
        self.referral.save(update_fields=["report", "report_ready"])
        self.assertEqual(self.status_for(hospital_user), 200)
        self.assertEqual(self.status_for(other_hospital_user), 403)


class BulkImportWorkflowTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.media_temp = tempfile.TemporaryDirectory()
        self.clinical_temp = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE=False, BULK_STAGING_ROOT=self.temp.name, CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE=False, PRIVATE_CLINICAL_ASSETS_ROOT=self.clinical_temp.name, MEDIA_ROOT=self.media_temp.name)
        self.settings_override.enable()
        self.clinic = Organization.objects.create(clinic_id="SYN-CLINIC", name="Synthetic Clinic", organization_type="clinic")
        self.hospital = Organization.objects.create(clinic_id="SYN-HOSP", name="Synthetic Hospital", organization_type="hospital")
        self.branch = OrganizationBranch.objects.create(organization=self.clinic, branch_code="MAIN", name="Main", is_head_office=True)
        self.session = AssessmentServiceSession.objects.create(
            service_date=date(2026, 8, 15), location_type="clinic", participating_organization=self.clinic,
            service_branch=self.branch, provider_type="sentinel", created_by=User.objects.create_user("session-owner"), status="active",
        )
        self.patient = Patient.objects.create(patient_id="SYN-PAT", first_name="Synthetic", last_name="Patient", date_of_birth=date(1980, 1, 1), sex="female", assigned_clinic=self.clinic, assigned_branch=self.branch)
        self.master_id = ensure_master_identity(self.patient).sentinel_patient_id
        self.referral = HospitalReferral.objects.create(source_hospital=self.hospital, patient=self.patient, first_name="Synthetic", last_name="Patient", hospital_mrn="0047", reason_for_referral="Synthetic", matched_clinic=self.clinic, matched_branch=self.branch)
        self.encounter = ScreeningEncounter.objects.create(encounter_id="SYN-ENC", patient=self.patient, encounter_date=self.session.service_date, originating_organization=self.clinic, service_branch=self.branch, service_session=self.session, hospital_referral=self.referral)
        self.user = User.objects.create_user("uploader")
        self.user.groups.add(Group.objects.create(name="clinic_screener"))
        UserOrganization.objects.create(user=self.user, organization=self.clinic)
        UserBranchAccess.objects.create(user=self.user, branch=self.branch, is_default=True)
        self.other = User.objects.create_user("other")
        other_org = Organization.objects.create(clinic_id="OTHER", name="Other", organization_type="clinic")
        other_branch = OrganizationBranch.objects.create(organization=other_org, branch_code="MAIN", name="Main")
        UserOrganization.objects.create(user=self.other, organization=other_org)
        UserBranchAccess.objects.create(user=self.other, branch=other_branch)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def tearDown(self):
        self.settings_override.disable()
        self.temp.cleanup()
        self.media_temp.cleanup()
        self.clinical_temp.cleanup()

    def create_import(self, archive=None, key="synthetic-key"):
        return self.client.post(
            reverse("bulk-import-create"),
            {"service_session": self.session.id, "branch": self.branch.id, "idempotency_key": key,
             "archive": SimpleUploadedFile("synthetic.zip", archive or remidio_archive(), content_type="application/zip")},
            format="multipart",
        )

    def test_realistic_inner_zip_previews_without_attachment_and_skips_pdf(self):
        archive = remidio_archive(extra=[("Synthetic_Patient_0047_15-08-2026/Images/report.pdf", b"%PDF-1.4 synthetic")])
        response = self.create_import(archive)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["skipped_count"], 1)
        self.assertEqual(response.data["groups"][0]["mrn"], "0047")
        self.assertEqual(response.data["groups"][0]["encounter"]["encounter_id"], self.encounter.encounter_id)
        self.assertFalse(ImageUpload.objects.exists())
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.master_patient.sentinel_patient_id, self.master_id)

    def test_preview_requires_authentication_and_scope_and_has_no_storage_url(self):
        created = self.create_import().data
        item = created["groups"][0]["items"][0]
        self.assertNotIn("storage", str(created).lower())
        self.assertNotIn("bucket", str(created).lower())
        preview = reverse("bulk-import-preview", args=[created["import_id"], item["item_id"]])
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(preview).status_code, {401, 403})
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(preview).status_code, 400)
        self.client.force_authenticate(self.user)
        response = self.client.get(preview)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store, max-age=0")
        response.close()

    def test_losing_upload_role_revokes_preview_access(self):
        created = self.create_import().data
        item = created["groups"][0]["items"][0]
        self.user.groups.clear()
        response = self.client.get(reverse("bulk-import-preview", args=[created["import_id"], item["item_id"]]))
        self.assertEqual(response.status_code, 400)

    def test_manual_laterality_atomic_confirmation_and_retry(self):
        created = self.create_import().data
        group = created["groups"][0]
        decisions = {group["items"][0]["item_id"]: "right", group["items"][1]["item_id"]: "left"}
        resolved = self.client.patch(reverse("bulk-import-group-resolve", args=[created["import_id"], group["group_id"]]), {"decisions": decisions}, format="json")
        self.assertEqual(resolved.status_code, 200, resolved.data)
        confirm_url = reverse("bulk-import-confirm", args=[created["import_id"]])
        self.assertEqual(self.client.post(confirm_url, {}, format="json").status_code, 200)
        self.assertEqual(BulkImageAttachment.objects.count(), 2)
        upload = ImageUpload.objects.first()
        self.assertEqual(upload.storage_kind, "private_clinical")
        self.assertEqual(upload.image_file.name, "")
        self.assertEqual(upload.dataset_eligibility, "excluded")
        self.assertTrue(os.path.exists(os.path.join(self.clinical_temp.name, upload.private_object_key)))
        self.user.groups.add(Group.objects.get_or_create(name="optometrist")[0])
        content = self.client.get(reverse("image-upload-content", args=[upload.pk]))
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content["Cache-Control"], "private, no-store, max-age=0")
        content.close()
        self.assertEqual(self.client.post(confirm_url, {}, format="json").status_code, 200)
        self.assertEqual(BulkImageAttachment.objects.count(), 2)

    def _resolved_import(self, key="resolved"):
        created = self.create_import(key=key).data
        group = created["groups"][0]
        decisions = {group["items"][0]["item_id"]: "right", group["items"][1]["item_id"]: "left"}
        self.client.patch(reverse("bulk-import-group-resolve", args=[created["import_id"], group["group_id"]]), {"decisions": decisions}, format="json")
        return created

    @patch("uploads.bulk_import.save_private_copy", side_effect=OSError("unavailable"))
    def test_permanent_copy_failure_is_retryable_without_database_attachments(self, mocked):
        created = self._resolved_import("copy-fail")
        url = reverse("bulk-import-confirm", args=[created["import_id"]])
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        self.assertFalse(ImageUpload.objects.exists())
        bulk_import = BulkImageImport.objects.get(import_id=created["import_id"])
        self.assertEqual(bulk_import.status, "preview")
        mocked.side_effect = None
        mocked.side_effect = lambda key, source: __import__("uploads.clinical_assets", fromlist=["save_private_copy"]).save_private_copy(key=key, source=source)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 200)

    def test_database_failure_preserves_prepared_objects_for_safe_retry(self):
        created = self._resolved_import("db-fail")
        url = reverse("bulk-import-confirm", args=[created["import_id"]])
        with patch("uploads.bulk_import.BulkImageAttachment.objects.create", side_effect=RuntimeError("db failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(url, {}, format="json")
        self.assertFalse(ImageUpload.objects.exists())
        prepared = BulkImageImportItem.objects.filter(permanent_copy_status="copied")
        self.assertEqual(prepared.count(), 2)
        self.assertTrue(all(os.path.exists(os.path.join(self.clinical_temp.name, item.permanent_object_key)) for item in prepared))
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 200)
        self.assertEqual(BulkImageAttachment.objects.count(), 2)

    def test_partial_permanent_copy_is_reused_on_retry(self):
        created = self._resolved_import("partial-copy")
        url = reverse("bulk-import-confirm", args=[created["import_id"]])
        calls = 0
        def partial_failure(*, key, source):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("second copy failed")
            return real_save_private_copy(key=key, source=source)
        with patch("uploads.bulk_import.save_private_copy", side_effect=partial_failure):
            self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        self.assertEqual(BulkImageImportItem.objects.filter(permanent_copy_status="copied").count(), 1)
        self.assertFalse(ImageUpload.objects.exists())
        with patch("uploads.bulk_import.save_private_copy", wraps=real_save_private_copy) as retry_copy:
            self.assertEqual(self.client.post(url, {}, format="json").status_code, 200)
            self.assertEqual(retry_copy.call_count, 1)

    def test_orphan_cleanup_failure_is_recorded_without_touching_attachment(self):
        created = self._resolved_import("orphan-cleanup")
        url = reverse("bulk-import-confirm", args=[created["import_id"]])
        with patch("uploads.bulk_import.BulkImageAttachment.objects.create", side_effect=RuntimeError("db failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(url, {}, format="json")
        bulk_import = BulkImageImport.objects.get(import_id=created["import_id"])
        with patch("django.core.files.storage.FileSystemStorage.delete", side_effect=OSError("delete failed")):
            self.assertFalse(cleanup_uncommitted_private_assets(bulk_import))
        self.assertEqual(BulkImageImportItem.objects.filter(permanent_cleanup_pending=True).count(), 2)

    def test_staging_cleanup_failure_does_not_damage_confirmed_assets(self):
        created = self._resolved_import("staging-cleanup")
        self.assertEqual(self.client.post(reverse("bulk-import-confirm", args=[created["import_id"]]), {}, format="json").status_code, 200)
        bulk_import = BulkImageImport.objects.get(import_id=created["import_id"])
        with patch("django.core.files.storage.FileSystemStorage.delete", side_effect=OSError("delete failed")):
            self.assertFalse(cleanup_import(bulk_import))
        bulk_import.refresh_from_db()
        self.assertTrue(bulk_import.cleanup_pending)
        self.assertEqual(BulkImageAttachment.objects.count(), 2)
        self.assertTrue(all(os.path.exists(os.path.join(self.clinical_temp.name, upload.private_object_key)) for upload in ImageUpload.objects.all()))

    def test_confirmation_lease_rejects_concurrent_request(self):
        created = self._resolved_import("concurrent")
        BulkImageImport.objects.filter(import_id=created["import_id"]).update(status="confirming")
        response = self.client.post(reverse("bulk-import-confirm", args=[created["import_id"]]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ImageUpload.objects.exists())

    def test_private_asset_access_is_revoked_with_scope(self):
        created = self._resolved_import("private-access")
        self.client.post(reverse("bulk-import-confirm", args=[created["import_id"]]), {}, format="json")
        upload = ImageUpload.objects.first()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(reverse("image-upload-content", args=[upload.pk])).status_code, 403)

    def test_confirmation_blocked_until_every_image_has_manual_decision(self):
        created = self.create_import().data
        response = self.client.post(reverse("bulk-import-confirm", args=[created["import_id"]]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ImageUpload.objects.exists())

    def test_extra_image_requires_rejection_and_existing_eye_conflict_blocks(self):
        extra = [("Synthetic_Patient_0047_15-08-2026/Images/3.00.00.001_image.jpg", image_bytes())]
        created = self.create_import(remidio_archive(extra=extra)).data
        group = created["groups"][0]
        decisions = {
            group["items"][0]["item_id"]: "right",
            group["items"][1]["item_id"]: "left",
            group["items"][2]["item_id"]: "rejected",
        }
        self.client.patch(reverse("bulk-import-group-resolve", args=[created["import_id"], group["group_id"]]), {"decisions": decisions}, format="json")
        ImageUpload.objects.create(image_upload_id="EXISTING", encounter=self.encounter, patient=self.patient, eye_laterality="right", image_file=SimpleUploadedFile("existing.jpg", image_bytes(), content_type="image/jpeg"))
        self.assertEqual(self.client.post(reverse("bulk-import-confirm", args=[created["import_id"]]), {}, format="json").status_code, 400)
        self.assertEqual(BulkImageAttachment.objects.count(), 0)

    def test_duplicate_archive_retry_is_idempotent(self):
        archive = remidio_archive()
        first = self.create_import(archive)
        second = self.create_import(archive)
        third = self.create_import(archive, "different-key")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.data["import_id"], second.data["import_id"])
        self.assertEqual(first.data["import_id"], third.data["import_id"])
        self.assertEqual(BulkImageImport.objects.count(), 1)

    def test_deeper_archive_and_traversal_are_rejected(self):
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("nested.zip", b"PK\x03\x04")
        self.assertEqual(self.create_import(remidio_archive(extra=[("Synthetic_Patient_0047_15-08-2026/Images/nested.zip", nested.getvalue())]), "nested").status_code, 400)
        self.assertEqual(self.create_import(remidio_archive(extra=[("../escape.jpg", image_bytes())]), "traversal").status_code, 400)

    def test_corrupt_disguised_image_and_pdf_are_rejected_safely(self):
        self.assertEqual(self.create_import(remidio_archive(extra=[("Synthetic_Patient_0047_15-08-2026/Images/bad.jpg", b"not-jpeg")]), "bad-image").status_code, 400)
        self.assertEqual(self.create_import(remidio_archive(extra=[("Synthetic_Patient_0047_15-08-2026/Images/bad.pdf", b"not-pdf")]), "bad-pdf").status_code, 400)

    def test_duplicate_eligible_mrn_is_ambiguous_and_name_is_not_fallback(self):
        second = Patient.objects.create(patient_id="SYN-PAT-2", first_name="Synthetic", last_name="Patient", date_of_birth=date(1981, 1, 1), sex="male", assigned_clinic=self.clinic, assigned_branch=self.branch)
        HospitalReferral.objects.create(source_hospital=self.hospital, patient=second, first_name="Synthetic", last_name="Patient", hospital_mrn="0047", reason_for_referral="Synthetic", matched_clinic=self.clinic, matched_branch=self.branch)
        second_referral = HospitalReferral.objects.filter(patient=second).get()
        ScreeningEncounter.objects.create(encounter_id="SYN-ENC-2", patient=second, encounter_date=self.session.service_date, originating_organization=self.clinic, service_branch=self.branch, service_session=self.session, hospital_referral=second_referral)
        ambiguous = self.create_import(key="ambiguous").data["groups"][0]
        self.assertIsNone(ambiguous["encounter"])
        self.assertEqual(ambiguous["safe_issue_code"], "ambiguous_or_unmatched_mrn")
        unmatched = self.create_import(remidio_archive(root="Synthetic_Patient_9999_15-08-2026"), "unmatched").data["groups"][0]
        self.assertIsNone(unmatched["encounter"])

    def test_manual_resolution_does_not_change_identity_or_referral(self):
        created = self.create_import(remidio_archive(root="Synthetic_Patient_9999_15-08-2026"), "manual").data
        before_referral = self.referral.referral_id
        response = self.client.patch(reverse("bulk-import-group-resolve", args=[created["import_id"], created["groups"][0]["group_id"]]), {"encounter": self.encounter.id, "decisions": {}}, format="json")
        self.assertEqual(response.status_code, 200)
        self.patient.refresh_from_db(); self.referral.refresh_from_db()
        self.assertEqual(self.patient.master_patient.sentinel_patient_id, self.master_id)
        self.assertEqual(self.referral.referral_id, before_referral)
        self.assertEqual(self.referral.hospital_mrn, "0047")

    def test_cancellation_removes_staged_objects(self):
        created = self.create_import().data
        bulk_import = BulkImageImport.objects.get(import_id=created["import_id"])
        keys = list(bulk_import.groups.values_list("items__staged_object_key", flat=True))
        response = self.client.delete(reverse("bulk-import-detail", args=[created["import_id"]]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(not os.path.exists(os.path.join(self.temp.name, key)) for key in keys if key))

    def test_ops_registry_labels_existing_identity_structures(self):
        ops_user = User.objects.create_user("ops-identity")
        ops_user.groups.add(Group.objects.get_or_create(name="ops_admin")[0])
        self.client.force_authenticate(ops_user)
        listing = self.client.get("/api/ops/patients/")
        self.assertEqual(listing.status_code, 200)
        row = next(item for item in listing.data if item["id"] == self.patient.id)
        self.assertEqual(row["sentinel_patient_id"], self.master_id)
        detail = self.client.get(f"/api/ops/patients/{self.patient.id}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["identities"]["sentinel_patient_id"], self.master_id)
        self.assertEqual(detail.data["identities"]["referrals"][0]["hospital_mrn"], "0047")
        self.assertEqual(detail.data["identities"]["referrals"][0]["referral_id"], self.referral.referral_id)
        self.assertEqual(detail.data["identities"]["encounters"][0]["encounter_id"], self.encounter.encounter_id)
