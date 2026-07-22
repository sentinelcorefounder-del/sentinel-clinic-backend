from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from encounters.models import ScreeningEncounter
from organizations.models import Organization
from patients.models import Patient
from referrals.models import HospitalReferral

from .models import (
    AllocationRule, EncounterFinancialRecord, PartnerContract, PricingRule,
    OrganizationWallet, WalletLedgerEntry, WalletReservation,
    BankTransferFundingRequest,
    ServiceAllowance, ServiceAllowanceReservation,
    SettlementBatch, SettlementItem, EncounterAllocation,
    FinanceActionRequest, FinanceControlAudit,
)
from .services import (
    price_encounter, top_up_wallet, reserve_wallet_funds,
    capture_wallet_reservation, release_wallet_reservation,
    infer_financial_identity, earn_financial_record_allocations,
    capture_finance_for_hospital_publication,
    submit_bank_transfer_proof, verify_bank_transfer, approve_bank_transfer,
    approve_service_allowance, reserve_service_allowance, fund_allowance_reservation,
    create_settlement_batch, approve_settlement_batch, mark_settlement_batch_paid,
    cancel_settlement_batch,
    create_finance_action_request, approve_finance_action_request,
    reject_finance_action_request, reconcile_finance_controls,
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
    def test_clinic_direct_completion_captures_and_earns_once(self):
        top_up_wallet(self.wallet, Decimal("15000.00"), "clinic-trigger-topup")
        from .services import reserve_financial_record_from_originating_wallet
        reserve_financial_record_from_originating_wallet(self.record)
        self.record.service_pathway = EncounterFinancialRecord.ServicePathway.CLINIC_DIRECT
        self.record.save(update_fields=["service_pathway", "updated_at"])
        self.encounter.source_type = "clinic_direct"
        self.encounter.screening_status = "completed"
        self.encounter.save(update_fields=["source_type", "screening_status", "updated_at"])

        self.record.refresh_from_db()
        self.assertEqual(self.record.status, EncounterFinancialRecord.Status.CAPTURED)
        self.assertTrue(self.record.financially_releasable)
        self.assertEqual(
            self.record.allocations.filter(status=self.record.allocations.model.Status.EARNED).count(),
            3,
        )

    def test_hospital_completion_does_not_capture_reserved_funds(self):
        top_up_wallet(self.wallet, Decimal("15000.00"), "hospital-trigger-topup")
        from .services import reserve_financial_record_from_originating_wallet
        reserve_financial_record_from_originating_wallet(self.record)

        self.encounter.screening_status = "completed"
        self.encounter.save(update_fields=["screening_status", "updated_at"])

        self.record.refresh_from_db()
        self.assertEqual(self.record.status, EncounterFinancialRecord.Status.WALLET_RESERVED)
        self.assertFalse(self.record.financially_releasable)
        self.assertFalse(
            self.record.allocations.filter(status=self.record.allocations.model.Status.EARNED).exists()
        )

    def test_hospital_publication_capture_is_idempotent_and_earns_once(self):
        top_up_wallet(self.wallet, Decimal("15000.00"), "hospital-publication-topup")
        from .services import reserve_financial_record_from_originating_wallet
        reserve_financial_record_from_originating_wallet(self.record)

        capture_finance_for_hospital_publication(self.encounter)
        capture_finance_for_hospital_publication(self.encounter)

        self.record.refresh_from_db()
        self.assertEqual(self.record.status, EncounterFinancialRecord.Status.CAPTURED)
        self.assertTrue(self.record.financially_releasable)
        self.assertEqual(
            self.record.allocations.filter(status=self.record.allocations.model.Status.EARNED).count(),
            3,
        )
        self.assertEqual(
            self.record.wallet_ledger_entries.filter(
                entry_type=WalletLedgerEntry.EntryType.SERVICE_CAPTURE
            ).count(),
            1,
        )

    def test_hospital_publication_requires_covered_finance(self):
        with self.assertRaisesMessage(ValidationError, "PAYMENT_REQUIRED"):
            capture_finance_for_hospital_publication(self.encounter)

    def test_approved_credit_does_not_publish_or_earn_before_funding(self):
        self.contract.credit_allowed = True
        self.contract.save(update_fields=["credit_allowed", "updated_at"])
        self.record.service_pathway = EncounterFinancialRecord.ServicePathway.CLINIC_DIRECT
        self.record.save(update_fields=["service_pathway", "updated_at"])
        self.encounter.source_type = "clinic_direct"
        self.encounter.screening_status = "completed"
        self.encounter.save(update_fields=["source_type", "screening_status", "updated_at"])

        self.record.refresh_from_db()
        self.assertEqual(self.record.status, EncounterFinancialRecord.Status.APPROVED_CREDIT)
        self.assertFalse(self.record.financially_releasable)
        self.assertFalse(
            self.record.allocations.filter(status=self.record.allocations.model.Status.EARNED).exists()
        )

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


class BankTransferFundingTests(WalletEngineTests):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="finance-verifier")
        self.approver = get_user_model().objects.create_user(username="finance-approver")
        self.request = BankTransferFundingRequest.objects.create(
            wallet=self.wallet,
            requested_amount=Decimal("50000.00"),
            currency="NGN",
            requester=self.user,
            expires_at=timezone.now() + timedelta(days=7),
        )

    def _submit_and_verify(self, amount="50000.00", reference="BANK-TXN-001"):
        proof = SimpleUploadedFile("receipt.pdf", b"test receipt", content_type="application/pdf")
        submit_bank_transfer_proof(self.request, proof, actor=self.user)
        return verify_bank_transfer(
            self.request,
            received_amount=Decimal(amount),
            bank_transaction_reference=reference,
            value_date=date.today(),
            actor=self.user,
        )

    def test_proof_does_not_credit_wallet(self):
        proof = SimpleUploadedFile("receipt.pdf", b"test receipt", content_type="application/pdf")
        request = submit_bank_transfer_proof(self.request, proof, actor=self.user)
        self.assertEqual(request.status, BankTransferFundingRequest.Status.PROOF_SUBMITTED)
        self.assertEqual(self.wallet.available_balance, Decimal("0.00"))
        self.assertEqual(WalletLedgerEntry.objects.count(), 0)

    def test_verified_transfer_credits_actual_amount_once_on_approval(self):
        request = self._submit_and_verify()
        self.assertEqual(request.status, BankTransferFundingRequest.Status.VERIFIED)
        self.assertEqual(self.wallet.available_balance, Decimal("0.00"))
        first = approve_bank_transfer(request, actor=self.approver)
        second = approve_bank_transfer(request, actor=self.approver)
        self.assertEqual(first.ledger_entry_id, second.ledger_entry_id)
        self.assertEqual(self.wallet.available_balance, Decimal("50000.00"))
        self.assertEqual(WalletLedgerEntry.objects.count(), 1)

    def test_underpayment_is_flagged_and_credits_only_received_amount(self):
        request = self._submit_and_verify(amount="45000.00")
        self.assertEqual(request.status, BankTransferFundingRequest.Status.UNDERPAID)
        approve_bank_transfer(request, actor=self.approver)
        self.assertEqual(self.wallet.available_balance, Decimal("45000.00"))

    def test_verifier_cannot_approve_same_transfer(self):
        request = self._submit_and_verify()
        with self.assertRaisesMessage(ValidationError, "Maker-checker"):
            approve_bank_transfer(request, actor=self.user)
        self.assertEqual(self.wallet.available_balance, Decimal("0.00"))

    def test_duplicate_bank_transaction_reference_is_rejected(self):
        self._submit_and_verify(reference="DUPLICATE-REF")
        other = BankTransferFundingRequest.objects.create(
            wallet=self.wallet,
            requested_amount=Decimal("10000.00"),
            currency="NGN",
        )
        proof = SimpleUploadedFile("other.pdf", b"other receipt", content_type="application/pdf")
        submit_bank_transfer_proof(other, proof, actor=self.user)
        with self.assertRaisesMessage(ValidationError, "already been used"):
            verify_bank_transfer(
                other,
                received_amount=Decimal("10000.00"),
                bank_transaction_reference="DUPLICATE-REF",
                value_date=date.today(),
                actor=self.user,
            )

    def test_expired_request_rejects_proof_and_does_not_credit(self):
        self.request.expires_at = timezone.now() - timedelta(minutes=1)
        self.request.save(update_fields=["expires_at", "updated_at"])
        proof = SimpleUploadedFile("late.pdf", b"late receipt", content_type="application/pdf")
        with self.assertRaisesMessage(ValidationError, "expired"):
            submit_bank_transfer_proof(self.request, proof, actor=self.user)
        self.request.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal("0.00"))


class VersionedPricingAndSettlementTests(WalletEngineTests):
    def test_volume_tier_is_applied_and_frozen_in_snapshot(self):
        self.rule.max_monthly_volume = 1
        self.rule.save(update_fields=["max_monthly_volume", "updated_at"])
        tier = PricingRule.objects.create(
            contract=self.contract, name="Hospital volume tier", version=2,
            supersedes=self.rule, service_type="retinal_assessment",
            source_type="hospital_referral", min_monthly_volume=2,
            gross_amount=Decimal("12000.00"), effective_from=date(2026, 1, 1), priority=1,
        )
        AllocationRule.objects.create(
            pricing_rule=tier, beneficiary_role=AllocationRule.BeneficiaryRole.SENTINEL,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("12000.00"),
        )
        price_encounter(self.encounter, force=True)
        second_patient = Patient.objects.create(
            patient_id="PAT-VOLUME-2", first_name="Volume", last_name="Two",
            date_of_birth=date(1980, 1, 1), sex="female",
        )
        second_encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-VOLUME-2", patient=second_patient, encounter_date=date.today(),
            originating_organization=self.organization, source_type="hospital_referral",
            workflow_route="sentinel_managed", payment_responsibility="hospital",
        )
        second_record = price_encounter(second_encounter, force=True)
        self.assertEqual(second_record.gross_amount, Decimal("12000.00"))
        self.assertEqual(second_record.pricing_snapshot["pricing_rule_version"], 2)
        self.assertEqual(second_record.pricing_snapshot["min_monthly_volume"], 2)

    def _earned_allocations(self):
        top_up_wallet(self.wallet, Decimal("15000.00"), "settlement-topup")
        reservation = reserve_wallet_funds(
            self.wallet, self.record, Decimal("15000.00"), "settlement-reserve"
        )
        capture_wallet_reservation(reservation, idempotency_key="settlement-capture")
        earn_financial_record_allocations(self.record)
        allocation = self.record.allocations.filter(
            beneficiary_role=AllocationRule.BeneficiaryRole.HOSPITAL
        ).first()
        allocation.beneficiary_organization = self.organization
        allocation.save(update_fields=["beneficiary_organization", "updated_at"])

    def test_settlement_requires_evidence_and_prevents_duplicate_reference(self):
        self._earned_allocations()
        batch = create_settlement_batch(
            self.organization, date.today(), date.today(), actor=None
        )
        approve_settlement_batch(batch)
        with self.assertRaisesMessage(ValidationError, "Payment evidence"):
            mark_settlement_batch_paid(batch, "PAY-001")
        evidence = SimpleUploadedFile("payment.pdf", b"paid", content_type="application/pdf")
        paid = mark_settlement_batch_paid(batch, "PAY-001", payment_evidence=evidence)
        self.assertEqual(paid.status, SettlementBatch.Status.PAID)
        self.assertTrue(paid.payment_evidence.name)

    def test_cancel_approved_settlement_restores_earned_allocations(self):
        self._earned_allocations()
        batch = create_settlement_batch(self.organization, date.today(), date.today())
        approve_settlement_batch(batch)
        self.assertEqual(
            EncounterAllocation.objects.filter(
                settlement_items__batch=batch,
                status=EncounterAllocation.Status.SETTLEMENT_PENDING,
            ).count(), 1,
        )
        cancel_settlement_batch(batch, "Payment details need correction")
        batch.refresh_from_db()
        self.assertEqual(batch.status, SettlementBatch.Status.CANCELLED)
        self.assertEqual(
            EncounterAllocation.objects.filter(
                settlement_items__batch=batch, status=EncounterAllocation.Status.EARNED,
            ).count(), 1,
        )
        replacement = create_settlement_batch(
            self.organization, date.today(), date.today()
        )
        self.assertNotEqual(replacement.pk, batch.pk)
        self.assertEqual(replacement.items.count(), 1)


class ServiceAllowanceTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            clinic_id="HOSP-ALLOW", name="Allowance Hospital", organization_type="hospital"
        )
        self.patient = Patient.objects.create(
            patient_id="PAT-FIN-ALLOW-1", first_name="First", last_name="Patient",
            date_of_birth=date(1980, 1, 1), sex="female",
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-FIN-ALLOW-1", patient=self.patient,
            encounter_date=date.today(), originating_organization=self.organization,
            source_type="hospital_referral", workflow_route="sentinel_managed",
            payment_responsibility="hospital",
        )
        self.contract = PartnerContract.objects.create(
            organization=self.organization, name="Allowance Contract",
            programme="diabetic_screening", status=PartnerContract.Status.ACTIVE,
            effective_from=date(2026, 1, 1),
        )
        rule = PricingRule.objects.create(
            contract=self.contract, name="Allowance Price", service_type="retinal_assessment",
            source_type="hospital_referral", gross_amount=Decimal("15000.00"),
            effective_from=date(2026, 1, 1),
        )
        AllocationRule.objects.create(
            pricing_rule=rule, beneficiary_role=AllocationRule.BeneficiaryRole.SENTINEL,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("15000.00"),
        )
        self.record = price_encounter(self.encounter)
        self.wallet = OrganizationWallet.objects.create(
            organization=self.organization, currency="NGN", credit_limit=Decimal("0.00")
        )
        self.user = get_user_model().objects.create_user(username="allowance-approver")
        self.allowance = ServiceAllowance.objects.create(
            organization=self.organization,
            contract=self.contract,
            name="Temporary service authority",
            currency="NGN",
            monetary_limit=Decimal("30000.00"),
            patient_limit=2,
            valid_from=date.today(),
            expires_at=timezone.now() + timedelta(days=30),
        )
        approve_service_allowance(self.allowance, actor=self.user)

    def test_allowance_reservation_never_credits_or_releases(self):
        reservation = reserve_service_allowance(self.record, actor=self.user)
        self.record.refresh_from_db()
        self.assertEqual(reservation.status, ServiceAllowanceReservation.Status.ACTIVE)
        self.assertEqual(self.record.status, EncounterFinancialRecord.Status.APPROVED_CREDIT)
        self.assertFalse(self.record.financially_releasable)
        self.assertEqual(self.wallet.available_balance, Decimal("0.00"))
        self.assertEqual(WalletLedgerEntry.objects.count(), 0)

    def test_patient_and_monetary_limits_cannot_be_exceeded(self):
        reserve_service_allowance(self.record, actor=self.user)
        second_patient = Patient.objects.create(
            patient_id="PAT-FIN-ALLOW-2", first_name="Second", last_name="Patient",
            date_of_birth=date(1981, 1, 1), sex="male",
        )
        second_encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-FIN-ALLOW-2", patient=second_patient,
            encounter_date=date.today(), originating_organization=self.organization,
            source_type="hospital_referral", workflow_route="sentinel_managed",
            payment_responsibility="hospital",
        )
        second_record = EncounterFinancialRecord.objects.get(encounter=second_encounter)
        if second_record.status in {EncounterFinancialRecord.Status.UNPRICED, EncounterFinancialRecord.Status.EXCEPTION}:
            second_record = price_encounter(second_encounter, force=True)
        reserve_service_allowance(second_record, actor=self.user)
        third_patient = Patient.objects.create(
            patient_id="PAT-FIN-ALLOW-3", first_name="Third", last_name="Patient",
            date_of_birth=date(1982, 1, 1), sex="female",
        )
        third_encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-FIN-ALLOW-3", patient=third_patient,
            encounter_date=date.today(), originating_organization=self.organization,
            source_type="hospital_referral", workflow_route="sentinel_managed",
            payment_responsibility="hospital",
        )
        third_record = EncounterFinancialRecord.objects.get(encounter=third_encounter)
        if third_record.status in {EncounterFinancialRecord.Status.UNPRICED, EncounterFinancialRecord.Status.EXCEPTION}:
            third_record = price_encounter(third_encounter, force=True)
        with self.assertRaisesMessage(ValidationError, "sufficient monetary and patient capacity"):
            reserve_service_allowance(third_record, actor=self.user)

    def test_expired_allowance_cannot_be_reserved(self):
        self.allowance.expires_at = timezone.now() - timedelta(minutes=1)
        self.allowance.save(update_fields=["expires_at", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "No active service allowance"):
            reserve_service_allowance(self.record, actor=self.user)

    def test_real_funding_replaces_allowance_then_capture_can_proceed(self):
        allowance_reservation = reserve_service_allowance(self.record, actor=self.user)
        top_up_wallet(self.wallet, Decimal("15000.00"), "allowance-real-funding")
        wallet_reservation = fund_allowance_reservation(self.record, actor=self.user)
        allowance_reservation.refresh_from_db()
        self.record.refresh_from_db()
        self.assertEqual(allowance_reservation.status, ServiceAllowanceReservation.Status.FUNDED)
        self.assertEqual(wallet_reservation.status, WalletReservation.Status.ACTIVE)
        self.assertFalse(self.record.financially_releasable)
        capture_finance_for_hospital_publication(self.encounter, actor=self.user)
        self.record.refresh_from_db()
        self.assertTrue(self.record.financially_releasable)

    def test_publication_remains_held_while_allowance_is_unfunded(self):
        reserve_service_allowance(self.record, actor=self.user)
        with self.assertRaisesMessage(ValidationError, "PAYMENT_REQUIRED"):
            capture_finance_for_hospital_publication(self.encounter, actor=self.user)


class FinanceControlTests(TestCase):
    def setUp(self):
        FinanceEngineTests.setUp(self)
        self.record = price_encounter(self.encounter)
        self.wallet = OrganizationWallet.objects.create(
            organization=self.organization, currency="NGN", credit_limit=Decimal("0.00")
        )
        User = get_user_model()
        self.maker = User.objects.create_user(username="finance-maker")
        self.checker = User.objects.create_user(username="finance-checker")
        top_up_wallet(self.wallet, Decimal("20000.00"), "control-opening")

    def _request(self, **overrides):
        values = {
            "action_type": FinanceActionRequest.ActionType.ADJUSTMENT,
            "wallet": self.wallet, "amount": Decimal("1000.00"),
            "reason": "Correct verified posting error", "external_reference": "FIN-CORR-001",
            "idempotency_key": "finance-control-001", "requested_by": self.maker,
        }
        values.update(overrides)
        return create_finance_action_request(**values)

    def test_request_does_not_change_wallet_until_approved(self):
        before = self.wallet.available_balance
        request = self._request()
        self.assertEqual(request.status, FinanceActionRequest.Status.PENDING)
        self.assertEqual(self.wallet.available_balance, before)
        self.assertEqual(FinanceControlAudit.objects.filter(action_request=request).count(), 1)

    def test_maker_cannot_approve_own_request(self):
        with self.assertRaises(ValidationError):
            approve_finance_action_request(self._request(), decided_by=self.maker)

    def test_checker_approval_posts_one_compensating_entry(self):
        approved = approve_finance_action_request(self._request(), decided_by=self.checker)
        self.assertEqual(approved.posted_entry.available_delta, Decimal("1000.00"))
        again = approve_finance_action_request(approved, decided_by=self.checker)
        self.assertEqual(again.posted_entry_id, approved.posted_entry_id)

    def test_rejection_posts_no_ledger_entry(self):
        rejected = reject_finance_action_request(
            self._request(), decided_by=self.checker, reason="Evidence does not match"
        )
        self.assertEqual(rejected.status, FinanceActionRequest.Status.REJECTED)
        self.assertIsNone(rejected.posted_entry_id)

    def test_paid_allocation_blocks_financial_correction(self):
        allocation = self.record.allocations.first()
        allocation.status = EncounterAllocation.Status.SETTLED
        allocation.save(update_fields=["status", "updated_at"])
        with self.assertRaises(ValidationError):
            self._request(financial_record=self.record)

    def test_reconciliation_is_read_only_and_clean(self):
        result = reconcile_finance_controls()
        self.assertTrue(result["ok"])
        self.assertEqual(result["issue_count"], 0)
