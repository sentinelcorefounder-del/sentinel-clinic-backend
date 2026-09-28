from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from encounters.models import ScreeningEncounter
from organizations.models import Organization
from patients.models import Patient

from .models import (
    AllocationRule,
    EncounterFinancialRecord,
    PartnerContract,
    PricingRule,
)


class ClinicDirectFundingFailurePersistenceTests(TestCase):
    def test_unfunded_clinic_direct_encounter_keeps_pricing_snapshot_for_ops(self):
        clinic = Organization.objects.create(
            clinic_id="CLINIC-DIRECT-UNFUNDED",
            name="Clinic Direct Unfunded",
            organization_type="clinic",
        )
        patient = Patient.objects.create(
            patient_id="PAT-CLINIC-DIRECT-UNFUNDED",
            first_name="Finance",
            last_name="Regression",
            date_of_birth=date(1980, 1, 1),
            sex="female",
            assigned_clinic=clinic,
        )
        contract = PartnerContract.objects.create(
            organization=clinic,
            name="Combined Assessment Agreement",
            programme="combined_assessment",
            status=PartnerContract.Status.ACTIVE,
            effective_from=date(2026, 1, 1),
            credit_allowed=False,
        )
        rule = PricingRule.objects.create(
            contract=contract,
            name="Combined assessment pricing",
            service_type="combined_assessment",
            source_type="clinic_direct",
            payment_responsibility="clinic",
            gross_amount=Decimal("15000.00"),
            effective_from=date(2026, 1, 1),
        )
        AllocationRule.objects.create(
            pricing_rule=rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.SENTINEL,
            beneficiary_organization=clinic,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("15000.00"),
        )

        encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-CLINIC-DIRECT-UNFUNDED",
            patient=patient,
            encounter_date=timezone.localdate(),
            originating_organization=clinic,
            source_type="clinic_direct",
            workflow_route="sentinel_managed",
            payment_responsibility="clinic",
            programme="combined_assessment",
            service_package=ScreeningEncounter.ServicePackage.COMBINED,
            encounter_type="combined_assessment",
        )

        record = EncounterFinancialRecord.objects.get(encounter=encounter)
        self.assertEqual(record.status, EncounterFinancialRecord.Status.EXCEPTION)
        self.assertEqual(record.service_pathway, EncounterFinancialRecord.ServicePathway.CLINIC_DIRECT)
        self.assertEqual(record.payer_type, EncounterFinancialRecord.PayerType.ORGANIZATION)
        self.assertEqual(record.payment_method, EncounterFinancialRecord.PaymentMethod.WALLET)
        self.assertEqual(record.contract_id, contract.id)
        self.assertEqual(record.pricing_rule_id, rule.id)
        self.assertEqual(record.gross_amount, Decimal("15000.00"))
        self.assertEqual(record.outstanding_amount, Decimal("15000.00"))
        self.assertFalse(record.financially_releasable)
        self.assertIsNotNone(record.priced_at)
        self.assertEqual(record.charge_components.count(), 1)
        self.assertEqual(record.allocations.count(), 1)
        self.assertTrue(record.exception_reason)
