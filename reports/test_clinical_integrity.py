from datetime import date
import importlib
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.migrations.operations.special import RunPython
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from encounters.models import ScreeningEncounter
from organizations.models import Organization, OrganizationBranch
from patients.models import Patient
from reports.clinical_integrity import (
    accept_responsibility,
    create_version_if_changed,
    snapshot_checksum,
)
from reports.models import ReportStatusEvent, StructuredReport, StructuredReportVersion
from users.models import UserBranchAccess, UserOrganization


class RetinalReportClinicalIntegrityMigrationTests(SimpleTestCase):
    def test_legacy_baseline_runs_atomically_inside_non_atomic_migration(self):
        migration_module = importlib.import_module(
            "reports.migrations.0010_report_clinical_integrity"
        )

        self.assertIs(migration_module.Migration.atomic, False)
        self.assertIsInstance(migration_module.Migration.operations[-1], RunPython)
        self.assertIs(migration_module.Migration.operations[-1].atomic, True)


class RetinalReportClinicalIntegrityTests(TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE=False,
            PRIVATE_CLINICAL_ASSETS_ROOT=self.storage.name,
        )
        self.settings_override.enable()
        self.client = APIClient()
        self.clinic = Organization.objects.create(
            clinic_id="CLINIC-INTEGRITY", name="Integrity Clinic", organization_type="clinic"
        )
        self.other_clinic = Organization.objects.create(
            clinic_id="CLINIC-OTHER", name="Other Clinic", organization_type="clinic"
        )
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Main", is_head_office=True
        )
        self.other_branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="OTHER", name="Other"
        )
        self.patient = Patient.objects.create(
            patient_id="PAT-INTEGRITY", first_name="Synthetic", last_name="Patient",
            date_of_birth=date(1980, 1, 1), sex="female", consent_status="completed",
            assigned_clinic=self.clinic, assigned_branch=self.branch,
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-INTEGRITY", patient=self.patient,
            encounter_date=date.today(), service_branch=self.branch,
            workflow_route="sentinel_managed",
        )
        self.admin = self.user("admin", {"clinic_admin"}, self.clinic, self.branch)
        self.optometrist = self.user("opto", {"optometrist"}, self.clinic, self.branch)
        self.master = self.user(
            "master", {"clinic_admin", "optometrist"}, self.clinic, self.branch
        )
        self.reviewer = self.user(
            "reviewer", {"clinic_admin", "reviewer"}, self.clinic, self.branch
        )

    def tearDown(self):
        self.settings_override.disable()
        self.storage.cleanup()
        super().tearDown()

    def user(self, username, roles, clinic=None, branch=None, superuser=False):
        user = get_user_model().objects.create_user(
            username=username, password="test-pass", is_superuser=superuser, is_staff=superuser
        )
        for role in roles:
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        if clinic:
            UserOrganization.objects.create(user=user, organization=clinic)
        if branch:
            UserBranchAccess.objects.create(user=user, branch=branch, is_default=True)
        return user

    def payload(self, **extra):
        data = {
            "report_id": "RPT-INTEGRITY", "encounter": self.encounter.pk,
            "patient": self.patient.pk, "review_date": str(date.today()),
            "ungradable": True, "urgency_outcome": "image_retake",
            "recommendation": "Synthetic recommendation",
            "clinician_name": "Dr Synthetic", "professional_role": "Optometrist",
            "registration_number": "OD-SYNTH-1",
        }
        data.update(extra)
        return data

    def create_api(self, user=None, **extra):
        self.client.force_authenticate(user or self.optometrist)
        return self.client.post("/api/reports/", self.payload(**extra), format="json")

    def test_additive_role_matrix_and_credentials(self):
        self.assertEqual(self.create_api(self.admin).status_code, 403)
        created = self.create_api(self.master)
        self.assertEqual(created.status_code, 201, created.data)
        report = StructuredReport.objects.get(pk=created.data["id"])
        self.assertEqual(report.clinical_responsibility.authority_used, "optometrist")
        self.assertTrue(self.master.groups.filter(name="clinic_admin").exists())

    def test_qualified_reviewer_and_missing_credentials(self):
        missing = self.create_api(self.reviewer, registration_number="")
        self.assertEqual(missing.status_code, 400)
        allowed = self.create_api(self.reviewer, registration_number="REV-SYNTH-1")
        self.assertEqual(allowed.status_code, 201, allowed.data)
        report = StructuredReport.objects.get(pk=allowed.data["id"])
        self.assertEqual(report.clinical_responsibility.authority_used, "reviewer")
        self.assertTrue(self.reviewer.groups.filter(name="clinic_admin").exists())

    def test_superuser_without_clinical_role_cannot_author(self):
        superuser = self.user("root", set(), superuser=True)
        self.assertEqual(self.create_api(superuser).status_code, 403)

    def test_cross_branch_and_organization_are_blocked(self):
        wrong_branch = self.user("wrong-branch", {"optometrist"}, self.clinic, self.other_branch)
        wrong_org = self.user("wrong-org", {"optometrist"}, self.other_clinic)
        self.assertEqual(self.create_api(wrong_branch).status_code, 403)
        self.assertEqual(self.create_api(wrong_org).status_code, 403)

    def test_identity_is_immutable_and_duplicate_returns_reference(self):
        created = self.create_api()
        self.assertEqual(created.status_code, 201, created.data)
        report = StructuredReport.objects.get(pk=created.data["id"])
        duplicate = self.create_api(report_id="RPT-SECOND")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.data["existing_report"]["id"], report.pk)
        self.client.force_authenticate(self.optometrist)
        changed = self.client.patch(
            f"/api/reports/{report.pk}/",
            {"patient": 999999, "expected_version": report.lock_version}, format="json",
        )
        self.assertEqual(changed.status_code, 400)
        self.assertEqual(StructuredReport.objects.filter(encounter=self.encounter).count(), 1)

    def test_meaningful_versions_noop_and_stale_write(self):
        created = self.create_api()
        report = StructuredReport.objects.get(pk=created.data["id"])
        self.assertEqual(report.versions.count(), 1)
        self.client.force_authenticate(self.optometrist)
        no_op = self.client.patch(
            f"/api/reports/{report.pk}/",
            {"recommendation": report.recommendation, "expected_version": report.lock_version},
            format="json",
        )
        self.assertEqual(no_op.status_code, 200, no_op.data)
        self.assertEqual(report.versions.count(), 1)
        changed = self.client.patch(
            f"/api/reports/{report.pk}/",
            {"recommendation": "Corrected", "expected_version": report.lock_version},
            format="json",
        )
        self.assertEqual(changed.status_code, 200, changed.data)
        report.refresh_from_db()
        self.assertEqual(report.versions.count(), 2)
        prior = report.versions.get(version_number=1)
        self.assertEqual(prior.clinical_snapshot["recommendation"], "Synthetic recommendation")
        stale = self.client.patch(
            f"/api/reports/{report.pk}/",
            {"recommendation": "Stale overwrite", "expected_version": report.lock_version - 1},
            format="json",
        )
        self.assertEqual(stale.status_code, 409)
        report.refresh_from_db()
        self.assertEqual(report.recommendation, "Corrected")

    def test_checksum_and_append_only_records(self):
        created = self.create_api()
        report = StructuredReport.objects.get(pk=created.data["id"])
        version = report.versions.get()
        self.assertEqual(version.checksum_sha256, snapshot_checksum(version.clinical_snapshot))
        version.purpose = "clinical_edit"
        with self.assertRaises(ValidationError):
            version.save()
        event = report.status_events.get(event_type="created")
        event.note = "altered"
        with self.assertRaises(ValidationError):
            event.save()

    def test_takeover_requires_eligible_professional_and_reason(self):
        created = self.create_api()
        report = StructuredReport.objects.get(pk=created.data["id"])
        successor = self.user("successor", {"optometrist", "clinic_admin"}, self.clinic, self.branch)
        self.client.force_authenticate(successor)
        denied = self.client.patch(
            f"/api/reports/{report.pk}/",
            {"recommendation": "Takeover", "expected_version": report.lock_version,
             "clinician_name": "Dr Successor", "professional_role": "Optometrist",
             "registration_number": "OD-SYNTH-2"}, format="json",
        )
        self.assertEqual(denied.status_code, 400)
        allowed = self.client.patch(
            f"/api/reports/{report.pk}/",
            {"recommendation": "Takeover", "expected_version": report.lock_version,
             "clinician_name": "Dr Successor", "professional_role": "Optometrist",
             "registration_number": "OD-SYNTH-2", "takeover_reason": "Original clinician unavailable"},
            format="json",
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        report.refresh_from_db()
        responsibility = report.clinical_responsibility
        self.assertEqual(responsibility.original_clinician, self.optometrist)
        self.assertEqual(responsibility.current_clinician, successor)
        self.assertTrue(report.status_events.filter(event_type="responsibility_taken_over").exists())

    def test_submitted_is_read_only_and_delete_is_prohibited(self):
        created = self.create_api()
        report = StructuredReport.objects.get(pk=created.data["id"])
        self.client.force_authenticate(self.optometrist)
        submitted = self.client.post(
            f"/api/reports/{report.pk}/submit-to-ops/",
            {"expected_version": report.lock_version}, format="json",
        )
        self.assertEqual(submitted.status_code, 200, submitted.data)
        report.refresh_from_db()
        edit = self.client.patch(
            f"/api/reports/{report.pk}/",
            {"recommendation": "Forbidden", "expected_version": report.lock_version}, format="json",
        )
        self.assertEqual(edit.status_code, 403)
        self.assertEqual(self.client.delete(f"/api/reports/{report.pk}/").status_code, 405)

    def test_legacy_baseline_can_be_author_unknown(self):
        report = StructuredReport.objects.create(
            report_id="RPT-LEGACY", encounter=self.encounter, patient=self.patient,
            review_date=date.today(), ungradable=True, urgency_outcome="image_retake",
        )
        version = StructuredReportVersion.objects.create(
            report=report, version_number=1, clinical_snapshot={"recommendation": "legacy"},
            checksum_sha256=snapshot_checksum({"recommendation": "legacy"}),
            editor=None, responsibility_snapshot={"historical_author": "unknown"},
            purpose="legacy_baseline", legacy_pdf_unbound=True,
        )
        self.assertIsNone(version.editor)
        self.assertEqual(version.responsibility_snapshot["historical_author"], "unknown")
        self.assertTrue(version.legacy_pdf_unbound)
