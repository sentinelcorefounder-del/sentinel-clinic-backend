from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from encounters.models import ScreeningEncounter
from ops.models import OpsAuditLog
from organizations.models import Organization, OrganizationProfile
from patients.models import Patient
from referrals.models import HospitalReferral
from referrals.serializers import HospitalReferralSerializer
from reports.models import ReportStatusEvent, StructuredReport
from users.models import UserOrganization


class ReleaseControlTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.clinic = Organization.objects.create(
            clinic_id="CLINIC-RC", name="Release Clinic", organization_type="clinic"
        )
        self.hospital = Organization.objects.create(
            clinic_id="HOSP-RC", name="Release Hospital", organization_type="hospital"
        )
        self.other_hospital = Organization.objects.create(
            clinic_id="HOSP-OTHER", name="Other Hospital", organization_type="hospital"
        )
        self.clinic_user = self.make_user("clinic-rc", "clinic_admin", self.clinic)
        self.hospital_user = self.make_user("hospital-rc", "hospital_admin", self.hospital)
        self.other_hospital_user = self.make_user(
            "hospital-other", "hospital_admin", self.other_hospital
        )
        self.ops_user = self.make_user("ops-rc", "ops_admin", None)
        self.patient = Patient.objects.create(
            patient_id="PAT-RC",
            first_name="Release",
            last_name="Patient",
            date_of_birth=date(1980, 1, 1),
            sex="female",
            consent_status="completed",
            assigned_clinic=self.clinic,
            referral_id="REF-RC",
        )
        self.referral = HospitalReferral.objects.create(
            referral_id="REF-RC",
            source_hospital=self.hospital,
            patient=self.patient,
            matched_clinic=self.clinic,
            first_name="Release",
            last_name="Patient",
            dob=date(1980, 1, 1),
            patient_sex="female",
            reason_for_referral="Retinal assessment",
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-RC",
            patient=self.patient,
            encounter_date=date.today(),
            originating_organization=self.hospital,
            hospital_referral=self.referral,
            workflow_route="sentinel_managed",
        )

    def make_user(self, username, role, organization):
        user = get_user_model().objects.create_user(username=username, password="test-pass")
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        if organization:
            UserOrganization.objects.create(user=user, organization=organization)
        return user

    def report_payload(self, encounter=None, patient=None, report_id="RPT-RC"):
        return {
            "report_id": report_id,
            "encounter": (encounter or self.encounter).id,
            "patient": (patient or self.patient).id,
            "review_date": str(date.today()),
            "ungradable": True,
            "urgency_outcome": "image_retake",
            "recommendation": "Retake images",
        }

    def create_report(self):
        report = StructuredReport.objects.create(
            report_id="RPT-RC",
            encounter=self.encounter,
            patient=self.patient,
            review_date=date.today(),
            ungradable=True,
            urgency_outcome="image_retake",
        )
        return report

    def submit(self, report):
        self.client.force_authenticate(self.clinic_user)
        return self.client.post(f"/api/reports/{report.id}/submit-to-ops/", {}, format="json")

    def issue(self, report):
        self.client.force_authenticate(self.ops_user)
        return self.client.post(
            f"/api/ops/reports/{report.id}/approve/",
            {
                "signer_name": "Dr Ops",
                "signer_role": "Ophthalmologist",
                "signer_registration_number": "REG-1",
            },
            format="json",
        )

    def release(self, report):
        self.client.force_authenticate(self.ops_user)
        return self.client.post(
            f"/api/ops/distribution/{report.id}/release-hospital/", {}, format="json"
        )

    def test_create_update_and_duplicate_protection(self):
        self.client.force_authenticate(self.clinic_user)
        created = self.client.post("/api/reports/", self.report_payload(), format="json")
        self.assertEqual(created.status_code, 201, created.data)
        report_id = created.data["id"]
        updated = self.client.patch(
            f"/api/reports/{report_id}/",
            {
                "ungradable": True,
                "urgency_outcome": "image_retake",
                "recommendation": "Corrected",
            },
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(StructuredReport.objects.filter(encounter=self.encounter).count(), 1)
        self.assertEqual(StructuredReport.objects.get(pk=report_id).recommendation, "Corrected")

        duplicate = self.client.post(
            "/api/reports/", self.report_payload(report_id="RPT-RC-2"), format="json"
        )
        self.assertEqual(duplicate.status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StructuredReport.objects.create(
                report_id="RPT-RC-DB",
                encounter=self.encounter,
                patient=self.patient,
                review_date=date.today(),
            )

    def test_submission_is_link_only_and_resubmission_reuses_report(self):
        report = self.create_report()
        response = self.submit(report)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("report_pdf_url", response.data)
        report.refresh_from_db()
        self.referral.refresh_from_db()
        self.assertEqual(report.report_status, "submitted_to_ops")
        self.assertEqual(self.referral.report_id, report.id)
        self.assertFalse(self.referral.report_ready)
        self.assertEqual(self.referral.referral_status, "submitted_to_ops")
        self.assertEqual(self.referral.payout_status, "not_due")

        report.report_status = "returned_to_clinic"
        report.save(update_fields=["report_status", "updated_at"])
        second = self.submit(report)
        self.assertEqual(second.status_code, 200, second.data)
        report.refresh_from_db()
        self.assertEqual(report.resubmission_count, 1)
        self.assertEqual(StructuredReport.objects.filter(encounter=self.encounter).count(), 1)

    def test_issuance_does_not_release_or_expose_to_hospital(self):
        report = self.create_report()
        self.submit(report)
        response = self.issue(report)
        self.assertEqual(response.status_code, 200, response.data)
        report.refresh_from_db()
        self.referral.refresh_from_db()
        self.assertEqual(report.report_status, "issued")
        self.assertEqual(report.distribution_status, "awaiting_distribution")
        self.assertIsNone(report.hospital_released_at)
        self.assertFalse(self.referral.report_ready)

        self.client.force_authenticate(self.hospital_user)
        reports = self.client.get("/api/referrals/hospital/reports/")
        self.assertEqual(reports.status_code, 200)
        self.assertEqual(reports.data, [])
        referral = self.client.get(f"/api/referrals/hospital/referrals/{self.referral.id}/")
        self.assertFalse(referral.data["report_ready"])
        self.assertEqual(referral.data["report_pdf_url"], "")
        self.assertEqual(referral.data["report_status"], "submitted_to_ops")
        patient = self.client.get(f"/api/referrals/hospital/patients/{self.patient.id}/")
        self.assertEqual(patient.data["reports"], [])
        denied = self.client.get(f"/api/reports/{report.id}/pdf/")
        self.assertEqual(denied.status_code, 403)

    @patch("reports.pdf_renderer.ReportPDFRenderer.build", return_value=b"%PDF-test")
    def test_release_allows_only_correct_hospital_and_is_idempotent(self, _build):
        report = self.create_report()
        self.submit(report)
        self.issue(report)
        first = self.release(report)
        self.assertEqual(first.status_code, 200, first.data)
        report.refresh_from_db()
        self.referral.refresh_from_db()
        self.assertEqual(report.distribution_status, "released_to_hospital")
        self.assertIsNotNone(report.hospital_released_at)
        self.assertEqual(report.hospital_released_by, self.ops_user)
        self.assertTrue(self.referral.report_ready)
        event_count = ReportStatusEvent.objects.filter(
            report=report, event_type="released_to_hospital"
        ).count()
        audit_count = OpsAuditLog.objects.filter(
            entity_type="report", entity_id=report.id, action="report_released_to_hospital"
        ).count()
        second = self.release(report)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(
            ReportStatusEvent.objects.filter(report=report, event_type="released_to_hospital").count(),
            event_count,
        )
        self.assertEqual(
            OpsAuditLog.objects.filter(
                entity_type="report", entity_id=report.id, action="report_released_to_hospital"
            ).count(),
            audit_count,
        )

        self.client.force_authenticate(self.hospital_user)
        self.assertEqual(self.client.get("/api/referrals/hospital/reports/").data[0]["id"], report.id)
        self.assertEqual(self.client.get(f"/api/reports/{report.id}/pdf/").status_code, 200)
        self.client.force_authenticate(self.other_hospital_user)
        self.assertEqual(self.client.get(f"/api/reports/{report.id}/pdf/").status_code, 403)

    def test_compatibility_sync_cannot_force_release(self):
        report = self.create_report()
        self.referral.report = report
        self.referral.save(update_fields=["report", "updated_at"])
        with patch.dict("os.environ", {"SENTINEL_SYNC_TOKEN": "sync-secret"}):
            response = self.client.post(
                "/api/referrals/hospital/sync-status/",
                {
                    "referral_id": self.referral.referral_id,
                    "report_ready": True,
                    "referral_status": "completed",
                },
                format="json",
                HTTP_X_SENTINEL_SYNC_TOKEN="sync-secret",
            )
        self.assertEqual(response.status_code, 400)
        self.referral.refresh_from_db()
        self.assertFalse(self.referral.report_ready)
        self.assertNotEqual(self.referral.referral_status, "completed")

    def test_clinic_and_hospital_cannot_use_ops_release(self):
        report = self.create_report()
        report.report_status = "issued"
        report.distribution_status = "awaiting_distribution"
        report.save(update_fields=["report_status", "distribution_status", "updated_at"])
        for user in (self.clinic_user, self.hospital_user):
            self.client.force_authenticate(user)
            response = self.client.post(
                f"/api/ops/distribution/{report.id}/release-hospital/", {}, format="json"
            )
            self.assertEqual(response.status_code, 403)

    def test_return_allows_correction_and_resubmission_of_same_report(self):
        report = self.create_report()
        self.submit(report)
        self.client.force_authenticate(self.ops_user)
        returned = self.client.post(
            f"/api/ops/reports/{report.id}/return/",
            {"reason": "Please correct the grade"},
            format="json",
        )
        self.assertEqual(returned.status_code, 200, returned.data)
        self.client.force_authenticate(self.clinic_user)
        corrected = self.client.patch(
            f"/api/reports/{report.id}/",
            {
                "ungradable": True,
                "urgency_outcome": "image_retake",
                "recommendation": "Corrected after Ops return",
            },
            format="json",
        )
        self.assertEqual(corrected.status_code, 200, corrected.data)
        resubmitted = self.submit(report)
        self.assertEqual(resubmitted.status_code, 200, resubmitted.data)
        report.refresh_from_db()
        self.assertEqual(report.report_status, "submitted_to_ops")
        self.assertEqual(report.resubmission_count, 1)
        self.assertEqual(StructuredReport.objects.filter(encounter=self.encounter).count(), 1)

    def test_clinic_managed_issuance_still_requires_controlled_release(self):
        profile, _ = OrganizationProfile.objects.get_or_create(organization=self.clinic)
        profile.electronic_signature_required = True
        profile.save()
        self.encounter.workflow_route = "clinic_managed"
        self.encounter.save(update_fields=["workflow_route", "updated_at"])
        report = self.create_report()
        self.referral.report = report
        self.referral.save(update_fields=["report", "updated_at"])
        self.client.force_authenticate(self.clinic_user)
        denied = self.client.post(
            f"/api/reports/{report.id}/clinic-issue/",
            {
                "signer_name": "Clinic Signer",
                "signer_role": "Optometrist",
                "signer_registration_number": "OD-1",
            },
            format="json",
        )
        self.assertEqual(denied.status_code, 403)
        profile.workflow_mode = "clinic_managed"
        profile.save(update_fields=["workflow_mode", "can_issue_reports_directly", "updated_at"])
        response = self.client.post(
            f"/api/reports/{report.id}/clinic-issue/",
            {
                "signer_name": "Clinic Signer",
                "signer_role": "Optometrist",
                "signer_registration_number": "OD-1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        report.refresh_from_db()
        self.referral.refresh_from_db()
        self.assertEqual(report.distribution_status, "awaiting_distribution")
        self.assertFalse(self.referral.report_ready)
        self.client.force_authenticate(self.hospital_user)
        self.assertEqual(self.client.get(f"/api/reports/{report.id}/pdf/").status_code, 403)

    def test_legacy_ops_routes_delegate_to_canonical_transitions(self):
        report = self.create_report()
        self.submit(report)
        self.client.force_authenticate(self.ops_user)
        rejected = self.client.post(
            f"/api/reports/{report.id}/ops-reject/",
            {"note": "Correction required"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 200, rejected.data)
        report.refresh_from_db()
        self.assertEqual(report.report_status, "ops_rejected")
        self.assertTrue(
            ReportStatusEvent.objects.filter(report=report, event_type="rejected").exists()
        )
        self.client.force_authenticate(self.clinic_user)
        corrected = self.client.patch(
            f"/api/reports/{report.id}/",
            {
                "ungradable": True,
                "urgency_outcome": "image_retake",
                "recommendation": "Corrected after rejection",
            },
            format="json",
        )
        self.assertEqual(corrected.status_code, 200, corrected.data)
        self.assertEqual(StructuredReport.objects.filter(encounter=self.encounter).count(), 1)
