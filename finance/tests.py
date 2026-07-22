from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from encounters.models import ScreeningEncounter
from organizations.models import Organization
from patients.models import Patient
from referrals.models import HospitalReferral

from .models import (
    AllocationRule, EncounterFinancialRecord, PartnerContract, PricingRule,
    OrganizationWallet, WalletLedgerEntry, WalletReservation,
)
from .services import (
    price_encounter, top_up_wallet, reserve_wallet_funds,
    capture_wallet_reservation, release_wallet_reservation,
    infer_financial_identity, earn_financial_record_allocations,
)


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
        self.assertEqual(record.service_pathway, EncounterFinancialRecord.ServicePathway.HOSPITAL_REFERRED)
        self.assertEqual(record.payer_type, EncounterFinancialRecord.PayerType.ORGANIZATION)
        self.assertEqual(record.payment_method, EncounterFinancialRecord.PaymentMethod.WALLET)
        self.assertTrue(
            all(
                allocation.status == allocation.Status.PENDING_SERVICE
                for allocation in record.allocations.all()
            )
        )

    def test_patient_payment_identity_is_independent_from_pathway(self):
        self.encounter.payment_responsibility = "patient"
        self.encounter.save(update_fields=["payment_responsibility"])
        pathway, payer, collector, method = infer_financial_identity(self.encounter)
        self.assertEqual(pathway, EncounterFinancialRecord.ServicePathway.HOSPITAL_REFERRED)
        self.assertEqual(payer, EncounterFinancialRecord.PayerType.PATIENT)
        self.assertEqual(collector, EncounterFinancialRecord.CollectorType.SENTINEL)
        self.assertEqual(method, EncounterFinancialRecord.PaymentMethod.PAYSTACK)

    def test_dynamic_hospital_and_testing_clinic_beneficiaries_are_frozen(self):
        clinic = Organization.objects.create(
            clinic_id="CLINIC-001",
            name="Testing Clinic",
            organization_type="clinic",
        )
        referral = HospitalReferral.objects.create(
            source_hospital=self.organization,
            matched_clinic=clinic,
            patient=self.patient,
            first_name=self.patient.first_name,
            last_name=self.patient.last_name,
            reason_for_referral="Retinal assessment",
        )
        self.encounter.hospital_referral = referral
        self.encounter.originating_organization = clinic
        self.encounter.save(update_fields=["hospital_referral", "originating_organization"])

        dynamic_rule = PricingRule.objects.create(
            contract=self.contract,
            name="Dynamic multi-party allocation",
            service_type="retinal_assessment",
            source_type="hospital_referral",
            gross_amount=Decimal("15000.00"),
            effective_from=date(2026, 1, 1),
            priority=1,
        )
        AllocationRule.objects.create(
            pricing_rule=dynamic_rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.HOSPITAL,
            beneficiary_source=AllocationRule.BeneficiarySource.REFERRING_HOSPITAL,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("2500.00"),
        )
        AllocationRule.objects.create(
            pricing_rule=dynamic_rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.CLINIC,
            beneficiary_source=AllocationRule.BeneficiarySource.TESTING_CLINIC,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("7500.00"),
        )
        AllocationRule.objects.create(
            pricing_rule=dynamic_rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.SENTINEL,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("5000.00"),
        )

        record = price_encounter(self.encounter)
        hospital_allocation = record.allocations.get(
            beneficiary_role=AllocationRule.BeneficiaryRole.HOSPITAL
        )
        clinic_allocation = record.allocations.get(
            beneficiary_role=AllocationRule.BeneficiaryRole.CLINIC
        )
        self.assertEqual(hospital_allocation.beneficiary_organization, self.organization)
        self.assertEqual(clinic_allocation.beneficiary_organization, clinic)
        self.assertEqual(
            hospital_allocation.rule_snapshot["beneficiary_organization_id"], self.organization.id
        )

    def test_invalid_allocation_total_is_rejected(self):
        invalid_rule = PricingRule.objects.create(
            contract=self.contract,
            name="Invalid Hospital Pricing",
            service_type="retinal_assessment",
            source_type="hospital_referral",
            gross_amount=Decimal("15000.00"),
            effective_from=date(2026, 1, 1),
            priority=100,
        )

        AllocationRule.objects.create(
            pricing_rule=invalid_rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.HOSPITAL,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("2500.00"),
        )

        AllocationRule.objects.create(
            pricing_rule=invalid_rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.CLINIC,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("7500.00"),
        )

        # Total allocation is only ₦10,000 instead of ₦15,000.
        # This should be rejected by the pricing engine.
        with self.assertRaises(ValidationError):
            price_encounter(self.encounter)


class WalletEngineTests(FinanceEngineTests):
    def setUp(self):
        super().setUp()
        self.record = price_encounter(self.encounter)
        self.wallet = OrganizationWallet.objects.create(
            organization=self.organization,
            currency="NGN",
            credit_limit=Decimal("0.00"),
        )

    def test_top_up_is_idempotent(self):
        first = top_up_wallet(self.wallet, Decimal("20000.00"), "topup-001")
        second = top_up_wallet(self.wallet, Decimal("20000.00"), "topup-001")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.wallet.available_balance, Decimal("20000.00"))
        self.assertEqual(WalletLedgerEntry.objects.count(), 1)

    def test_reserve_moves_available_to_reserved(self):
        top_up_wallet(self.wallet, Decimal("15000.00"), "topup-002")
        reservation = reserve_wallet_funds(
            self.wallet,
            self.record,
            Decimal("15000.00"),
            "reserve-001",
        )
        self.assertEqual(reservation.status, WalletReservation.Status.ACTIVE)
        self.assertEqual(self.wallet.available_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("15000.00"))
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, EncounterFinancialRecord.Status.WALLET_RESERVED)

    def test_capture_consumes_reserved_funds_and_releases_report(self):
        top_up_wallet(self.wallet, Decimal("15000.00"), "topup-003")
        reservation = reserve_wallet_funds(
            self.wallet,
            self.record,
            Decimal("15000.00"),
            "reserve-002",
        )
        capture_wallet_reservation(reservation, idempotency_key="capture-001")
        reservation.refresh_from_db()
        self.record.refresh_from_db()
        self.assertEqual(reservation.status, WalletReservation.Status.CAPTURED)
        self.assertEqual(self.wallet.available_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.record.outstanding_amount, Decimal("0.00"))
        self.assertTrue(self.record.financially_releasable)
        self.assertEqual(self.record.status, EncounterFinancialRecord.Status.CAPTURED)

    def test_allocation_earning_is_explicit_and_idempotent(self):
        top_up_wallet(self.wallet, Decimal("15000.00"), "topup-earn")
        reservation = reserve_wallet_funds(
            self.wallet, self.record, Decimal("15000.00"), "reserve-earn"
        )
        capture_wallet_reservation(reservation, idempotency_key="capture-earn")
        self.assertFalse(
            self.record.allocations.filter(
                status=self.record.allocations.model.Status.EARNED
            ).exists()
        )
        earn_financial_record_allocations(self.record)
        earn_financial_record_allocations(self.record)
        self.assertEqual(
            self.record.allocations.filter(
                status=self.record.allocations.model.Status.EARNED
            ).count(),
            3,
        )
        self.assertEqual(
            self.record.audit_entries.filter(action="allocations_earned").count(), 1
        )

    def test_release_returns_reserved_funds(self):
        top_up_wallet(self.wallet, Decimal("15000.00"), "topup-004")
        reservation = reserve_wallet_funds(
            self.wallet,
            self.record,
            Decimal("15000.00"),
            "reserve-003",
        )
        release_wallet_reservation(reservation, idempotency_key="release-001")
        reservation.refresh_from_db()
        self.record.refresh_from_db()
        self.assertEqual(reservation.status, WalletReservation.Status.RELEASED)
        self.assertEqual(self.wallet.available_balance, Decimal("15000.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.record.status, EncounterFinancialRecord.Status.AWAITING_PAYMENT)

    def test_insufficient_funds_are_rejected(self):
        with self.assertRaises(ValidationError):
            reserve_wallet_funds(
                self.wallet,
                self.record,
                Decimal("15000.00"),
                "reserve-004",
            )

    def test_ledger_entries_are_immutable(self):
        entry = top_up_wallet(self.wallet, Decimal("1000.00"), "topup-005")
        entry.description = "Changed"
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()


class NegotiatedPricingAndAutomationTests(WalletEngineTests):
    def test_negotiated_price_is_reserved_not_global_default(self):
        negotiated_rule = PricingRule.objects.create(
            contract=self.contract,
            name="Negotiated Hospital Rate",
            service_type="retinal_assessment",
            source_type="hospital_referral",
            gross_amount=Decimal("12500.00"),
            effective_from=date(2026, 1, 1),
            priority=1,
        )
        AllocationRule.objects.create(
            pricing_rule=negotiated_rule,
            beneficiary_role=AllocationRule.BeneficiaryRole.SENTINEL,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("12500.00"),
        )
        self.record = price_encounter(self.encounter, force=True)
        top_up_wallet(self.wallet, Decimal("20000.00"), "negotiated-topup")
        from .services import reserve_financial_record_from_originating_wallet
        reservation = reserve_financial_record_from_originating_wallet(self.record)
        self.assertEqual(reservation.amount, Decimal("12500.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("7500.00"))

    def test_automatic_capture_uses_existing_reservation(self):
        top_up_wallet(self.wallet, Decimal("15000.00"), "automation-topup")
        from .services import (
            reserve_financial_record_from_originating_wallet,
            capture_financial_record_wallet_reservation,
        )
        reserve_financial_record_from_originating_wallet(self.record)
        capture_financial_record_wallet_reservation(self.record)
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, EncounterFinancialRecord.Status.CAPTURED)
        self.assertEqual(self.record.outstanding_amount, Decimal("0.00"))
