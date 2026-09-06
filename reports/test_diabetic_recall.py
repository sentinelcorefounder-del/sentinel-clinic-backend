from datetime import date

from django.contrib.auth.models import Group, User
from rest_framework.test import APITestCase

from encounters.models import ScreeningEncounter
from organizations.models import Organization, OrganizationBranch
from patients.models import Patient
from users.models import UserBranchAccess, UserOrganization
from reports.models import DiabeticRecall
from reports.recall_services import add_months, set_diabetic_recall


class DiabeticRecallTests(APITestCase):
    def setUp(self):
        self.clinic = Organization.objects.create(
            clinic_id="REC-CL",
            name="Recall Clinic",
            organization_type="clinic",
        )
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic,
            branch_code="MAIN",
            name="Main",
        )
        self.patient = Patient.objects.create(
            patient_id="REC-PAT",
            first_name="Recall",
            last_name="Patient",
            date_of_birth=date(1980, 1, 1),
            sex="female",
            assigned_clinic=self.clinic,
            assigned_branch=self.branch,
        )
        self.user = User.objects.create_user("recall-opto")
        self.user.groups.add(
            Group.objects.get_or_create(name="optometrist")[0]
        )

        UserOrganization.objects.create(
            user=self.user,
            organization=self.clinic,
        )
        UserBranchAccess.objects.create(
            user=self.user,
            branch=self.branch,
        )

    def encounter(
        self,
        programme="ocular_diagnostics",
        package="comprehensive_ocular_assessment",
        diabetic=True,
    ):
        return ScreeningEncounter.objects.create(
            encounter_id=f"REC-{programme}-{ScreeningEncounter.objects.count()}",
            patient=self.patient,
            encounter_date=date(2026, 8, 28),
            originating_organization=self.clinic,
            service_branch=self.branch,
            programme=programme,
            service_package=package,
            source_type="clinic_direct",
            workflow_route="clinic_managed",
            payment_responsibility="clinic",
            is_diabetic=diabetic,
        )

    def test_month_math_handles_month_end(self):
        self.assertEqual(
            add_months(date(2026, 2, 28), 12),
            date(2027, 2, 28),
        )
        self.assertEqual(
            add_months(date(2024, 2, 29), 12),
            date(2025, 2, 28),
        )

    def test_diabetic_ocular_encounter_can_schedule_recall(self):
        encounter = self.encounter()

        recall = set_diabetic_recall(
            encounter=encounter,
            months=12,
            actor=self.user,
        )

        self.assertEqual(recall.due_date, date(2027, 8, 28))
        self.assertEqual(recall.organization, self.clinic)

    def test_non_diabetic_encounter_rejected(self):
        encounter = self.encounter(diabetic=False)

        with self.assertRaises(Exception):
            set_diabetic_recall(
                encounter=encounter,
                months=12,
                actor=self.user,
            )

    def test_repeat_schedule_updates_same_record(self):
        encounter = self.encounter()

        first = set_diabetic_recall(
            encounter=encounter,
            months=12,
            actor=self.user,
        )

        second = set_diabetic_recall(
            encounter=encounter,
            months=6,
            actor=self.user,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            DiabeticRecall.objects.filter(encounter=encounter).count(),
            1,
        )
        self.assertEqual(second.recall_months, 6)

    def test_recall_endpoint_is_clinic_and_branch_scoped(self):
        encounter = self.encounter()

        self.client.force_authenticate(self.user)

        response = self.client.post(
            f"/api/reports/recalls/encounter/{encounter.pk}/",
            {"recall_months": 12},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["recall_due_date"],
            date(2027, 8, 28),
        )

    def test_all_relevant_templates_can_schedule_when_diabetic(self):
        templates = [
            (
                "ocular_diagnostics",
                "comprehensive_ocular_assessment",
            ),
            (
                "combined_assessment",
                "combined_diabetic_eye_health",
            ),
            (
                "diabetic_screening",
                "diabetic_retinal_assessment",
            ),
            (
                "eye_health_screening",
                "eye_health_screening",
            ),
        ]

        for programme, package in templates:
            with self.subTest(
                programme=programme,
                package=package,
            ):
                encounter = self.encounter(
                    programme=programme,
                    package=package,
                    diabetic=True,
                )

                recall = set_diabetic_recall(
                    encounter=encounter,
                    months=12,
                    actor=self.user,
                )

                self.assertEqual(recall.recall_months, 12)
                self.assertEqual(
                    recall.due_date,
                    date(2027, 8, 28),
                )