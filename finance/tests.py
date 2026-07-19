from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from encounters.models import ScreeningEncounter
from organizations.models import Organization
from patients.models import Patient

from .models import AllocationRule, EncounterFinancialRecord, PartnerContract, PricingRule
from .services import price_encounter


class FinanceEngineTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            clinic_id="HOSP-001",
            name="Test Hospital",
            organization_type="hospital",
        )
        self.patient = Patient.objects.create(
            patient_id="PAT-FIN-001",
            first_name="Test",
            last_name="Patient",
            date_of_birth=date(1980, 1, 1),
            sex="female",
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-FIN-001",
            patient=self.patient,
            encounter_date=date.today(),
            originating_organization=self.organization,
            source_type="hospital_referral",
            workflow_route="sentinel_managed",
            payment_responsibility="hospital",
        )
        self.contract = PartnerContract.objects.create(
            organization=self.organization,
            name="Hospital Programme",
            programme="diabetic_screening",
            status=PartnerContract.Status.ACTIVE,
            effective_from=date(2026, 1, 1),
        )
        self.rule = PricingRule.objects.create(
            contract=self.contract,
            name="Hospital Standard",
            service_type="retinal_assessment",
            source_type="hospital_referral",
            gross_amount=Decimal("15000.00"),
            effective_from=date(2026, 1, 1),
        )
        AllocationRule.objects.create(
            pricing_rule=self.rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.HOSPITAL,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("2500.00"),
        )
        AllocationRule.objects.create(
            pricing_rule=self.rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.CLINIC,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("7500.00"),
        )
        AllocationRule.objects.create(
            pricing_rule=self.rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.SENTINEL,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("5000.00"),
        )

    def test_financial_record_is_created_with_encounter(self):
        self.assertTrue(EncounterFinancialRecord.objects.filter(encounter=self.encounter).exists())

    def test_encounter_prices_and_allocates_exactly(self):
        record = price_encounter(self.encounter)
        self.assertEqual(record.gross_amount, Decimal("15000.00"))
        self.assertEqual(record.allocated_amount, Decimal("15000.00"))
        self.assertEqual(record.allocations.count(), 3)
        self.assertEqual(record.status, EncounterFinancialRecord.Status.AWAITING_PAYMENT)

    def test_invalid_allocation_total_is_rejected(self):
        self.rule.allocation_rules.last().delete()
        with self.assertRaises(ValidationError):
            price_encounter(self.encounter)
