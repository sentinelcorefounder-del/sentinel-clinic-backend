import tempfile
from datetime import date
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase
from organizations.models import Organization, OrganizationBranch
from patients.models import Patient
from encounters.models import ScreeningEncounter
from referrals.models import HospitalReferral
from users.models import UserOrganization, UserSecurityProfile
from reports.models import HistoricalReportDocument


class HistoricalReportTests(APITestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.clinic = Organization.objects.create(clinic_id="HIST-CLINIC", name="Clinic", organization_type="clinic")
        self.hospital = Organization.objects.create(clinic_id="HIST-HOSP", name="Hospital", organization_type="hospital")
        self.branch = OrganizationBranch.objects.create(organization=self.clinic, branch_code="MAIN", name="Main")
        self.patient = Patient.objects.create(patient_id="HIST-PAT", first_name="A", last_name="Patient", date_of_birth=date(1980,1,1), sex="female", assigned_clinic=self.clinic, assigned_branch=self.branch)
        self.referral = HospitalReferral.objects.create(source_hospital=self.hospital, patient=self.patient, first_name="A", last_name="Patient", reason_for_referral="Historical")
        self.encounter = ScreeningEncounter.objects.create(encounter_id="HIST-ENC", patient=self.patient, encounter_date=date.today(), originating_organization=self.clinic, service_branch=self.branch, source_type="hospital_referral", workflow_route="clinic_managed", payment_responsibility="patient", programme="ocular_diagnostics", encounter_type="ocular_assessment", hospital_referral=self.referral)
        self.clinician = get_user_model().objects.create_user("hist-clinician")
        self.clinician.groups.add(Group.objects.get_or_create(name="optometrist")[0])
        UserOrganization.objects.create(user=self.clinician, organization=self.clinic)
        UserSecurityProfile.objects.create(user=self.clinician, is_internal_sentinel_staff=False)
        self.hospital_user = get_user_model().objects.create_user("hist-hospital")
        self.hospital_user.groups.add(Group.objects.get_or_create(name="hospital_admin")[0])
        UserOrganization.objects.create(user=self.hospital_user, organization=self.hospital)
        UserSecurityProfile.objects.create(user=self.hospital_user, is_internal_sentinel_staff=False)

    def tearDown(self):
        self.override.disable()
        self.media.cleanup()

    def pdf(self):
        return SimpleUploadedFile("old-report.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf")

    def test_historical_upload_is_encounter_and_referral_scoped(self):
        self.client.force_authenticate(self.clinician)
        response = self.client.post(f"/api/reports/historical/encounter/{self.encounter.pk}/", {"document": self.pdf(), "report_date": "2026-08-01", "title": "Outside report", "hospital_visible": "true"}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        item = HistoricalReportDocument.objects.get()
        self.assertEqual(item.encounter, self.encounter); self.assertEqual(item.patient, self.patient); self.assertEqual(item.hospital_referral, self.referral); self.assertTrue(item.hospital_visible)

        self.client.force_authenticate(self.hospital_user)
        listing = self.client.get("/api/referrals/hospital/reports/")
        self.assertEqual(listing.status_code, 200)
        self.assertTrue(any(row.get("report_type") == "historical" and row.get("id") == item.id for row in listing.data))
        content = self.client.get(f"/api/reports/historical/{item.pk}/content/")
        self.assertEqual(content.status_code, 200)
        content.close()

    def test_historical_upload_does_not_create_structured_report(self):
        self.client.force_authenticate(self.clinician)
        response = self.client.post(f"/api/reports/historical/encounter/{self.encounter.pk}/", {"document": self.pdf(), "report_date": "2026-08-01", "hospital_visible": "false"}, format="multipart")
        self.assertEqual(response.status_code, 201)
        self.assertFalse(hasattr(self.encounter, "structured_report"))
