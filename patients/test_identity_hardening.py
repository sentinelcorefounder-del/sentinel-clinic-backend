from datetime import date

from django.test import TestCase

from organizations.models import Organization
from patients.identity_services import ensure_master_identity
from patients.models import MasterPatient, Patient


class MasterIdentityHardeningTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            clinic_id="IDENTITY-1",
            name="Identity Clinic",
            organization_type="clinic",
        )

    def patient(self, patient_id, phone):
        return Patient.objects.create(
            patient_id=patient_id,
            first_name="Ada",
            last_name="Okafor",
            date_of_birth=date(1980, 1, 2),
            sex="female",
            phone=phone,
            assigned_clinic=self.organization,
        )

    def test_same_person_from_two_routes_uses_one_master_identity(self):
        first = self.patient("CLINIC-001", "08012345678")
        second = self.patient("HOSPITAL-999", "+2348012345678")
        first_master = ensure_master_identity(first, self.organization, first.patient_id)
        second_master = ensure_master_identity(second, self.organization, second.patient_id)
        self.assertEqual(first_master.id, second_master.id)
        self.assertEqual(MasterPatient.objects.count(), 1)

    def test_sequence_issues_distinct_sentinel_ids(self):
        first = self.patient("LOCAL-001", "08011111111")
        second = Patient.objects.create(
            patient_id="LOCAL-002",
            first_name="Bola",
            last_name="Adewale",
            date_of_birth=date(1975, 3, 4),
            sex="male",
            phone="08022222222",
            assigned_clinic=self.organization,
        )
        ids = {
            ensure_master_identity(first).sentinel_patient_id,
            ensure_master_identity(second).sentinel_patient_id,
        }
        self.assertEqual(len(ids), 2)
