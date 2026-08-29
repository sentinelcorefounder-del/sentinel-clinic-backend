from datetime import date
from decimal import Decimal
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from encounters.models import AssessmentServiceSession, ScreeningEncounter
from finance.models import (
    AllocationRule,
    BankTransferFundingRequest,
    EncounterAllocation,
    EncounterFinancialRecord,
    EncounterSponsorship,
    FinanceActionRequest,
    OrganizationWallet,
    PartnerContract,
    PricingRule,
    ServiceAllowance,
    ServiceAllowanceReservation,
    ServicePartnerEarning,
    ServicePartnerSettlementBatch,
    TreasuryTransfer,
    WalletLedgerEntry,
)
from finance.services import (
    cancel_encounter_sponsorship,
    cancel_treasury_transfer,
    approve_finance_action_request,
    attach_encounter_to_service_session,
    capture_encounter_sponsorship,
    capture_financial_record_wallet_reservation,
    create_encounter_sponsorship,
    create_finance_action_request,
    create_treasury_transfer,
    decide_encounter_sponsorship,
    decide_treasury_transfer,
    record_treasury_transfer_execution,
    recognize_service_partner_earning,
    reverse_treasury_transfer,
    sentinel_treasury_summary,
    submit_encounter_sponsorship,
    submit_treasury_transfer,
    top_up_wallet,
)
from organizations.models import Organization, OrganizationBranch
from patients.models import Patient
from payments.models import PaymentTransaction
from payments.services.posting import post_verified_payment
from reports.models import StructuredReport
from users.models import UserBranchAccess, UserOrganization, UserSecurityProfile


class SponsorshipAndTreasuryTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.sentinel = Organization.objects.create(
            clinic_id="SENTINEL-TREASURY", name="Project Sentinel", organization_type="sentinel"
        )
        self.afri = Organization.objects.create(
            clinic_id="AFRI-SEPARATE", name="Afriophthalmics", organization_type="clinic"
        )
        self.clinic = Organization.objects.create(
            clinic_id="SPONSOR-CLINIC", name="Synthetic Sponsor Clinic", organization_type="clinic"
        )
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Main", is_head_office=True
        )
        self.patient = Patient.objects.create(
            patient_id="SPONSOR-PATIENT", first_name="Synthetic", last_name="Beneficiary",
            date_of_birth=date(1980, 1, 1), sex="female",
            assigned_clinic=self.clinic, assigned_branch=self.branch,
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="SPONSOR-ENCOUNTER", patient=self.patient,
            encounter_date=date.today(), originating_organization=self.clinic,
            service_branch=self.branch, source_type="clinic_direct",
            workflow_route="clinic_managed", payment_responsibility="patient",
            programme="ocular_diagnostics", encounter_type="ocular_assessment",
        )
        contract = PartnerContract.objects.create(
            organization=self.clinic, name="Synthetic Ocular Standard",
            programme="ocular_diagnostics", status=PartnerContract.Status.ACTIVE,
            effective_from=date(2026, 1, 1),
        )
        rule = PricingRule.objects.create(
            contract=contract, name="Ocular standard", service_type="ocular_assessment",
            gross_amount=Decimal("10000.00"), effective_from=date(2026, 1, 1),
        )
        AllocationRule.objects.create(
            pricing_rule=rule, beneficiary_role=AllocationRule.BeneficiaryRole.CLINIC,
            beneficiary_organization=self.clinic,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("6000.00"),
        )
        AllocationRule.objects.create(
            pricing_rule=rule, beneficiary_role=AllocationRule.BeneficiaryRole.SENTINEL,
            beneficiary_organization=self.sentinel,
            calculation_type=AllocationRule.CalculationType.FIXED,
            fixed_amount=Decimal("4000.00"),
        )
        self.wallet = OrganizationWallet.objects.create(organization=self.sentinel)
        self.afri_wallet = OrganizationWallet.objects.create(organization=self.afri)
        self.operator = self.user("sponsor-operator", "finance_operator", internal=True)
        self.approver = self.user("sponsor-approver", "finance_approver", internal=True)
        self.viewer = self.user("sponsor-viewer", "finance_viewer", internal=True)

    def tearDown(self):
        self.settings_override.disable()
        self.media.cleanup()

    def user(self, username, role, internal=False, superuser=False):
        user = get_user_model().objects.create_user(
            username, is_superuser=superuser, is_staff=superuser
        )
        if role:
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        UserSecurityProfile.objects.create(user=user, is_internal_sentinel_staff=internal)
        return user

    def fund(self, amount="25000.00"):
        entry = top_up_wallet(
            self.wallet, amount, f"synthetic-receipt:{amount}", reference="SYNTHETIC-VERIFIED"
        )
        BankTransferFundingRequest.objects.create(
            wallet=self.wallet, requested_amount=Decimal(amount), received_amount=Decimal(amount),
            status=BankTransferFundingRequest.Status.CREDITED,
            bank_transaction_reference=f"SYNTHETIC-{amount}", value_date=date.today(),
            ledger_entry=entry,
        )
        return entry

    def draft(self, key="sponsorship-one"):
        return create_encounter_sponsorship(
            encounter=self.encounter, sponsor_wallet=self.wallet,
            category=EncounterSponsorship.Category.COMPLIMENTARY,
            reason="Approved synthetic complimentary service", idempotency_key=key,
            actor=self.operator,
        )

    def test_sponsorship_retains_value_sets_patient_zero_and_creates_no_payment(self):
        item = self.draft()
        self.assertEqual(item.patient_amount, Decimal("0.00"))
        self.assertEqual(item.gross_service_value, Decimal("10000.00"))
        self.assertEqual(sum(Decimal(row["amount"]) for row in item.allocation_snapshot), Decimal("10000.00"))
        self.assertEqual(PaymentTransaction.objects.count(), 0)
        self.assertEqual(BankTransferFundingRequest.objects.count(), 0)
        self.assertEqual(ServiceAllowance.objects.count(), 0)
        self.assertEqual(ServiceAllowanceReservation.objects.count(), 0)
        self.assertEqual(ServicePartnerEarning.objects.count(), 0)

    def test_non_sentinel_wallet_cannot_sponsor_or_transfer(self):
        with self.assertRaisesMessage(Exception, "Sentinel wallet"):
            create_encounter_sponsorship(
                encounter=self.encounter, sponsor_wallet=self.afri_wallet,
                category=EncounterSponsorship.Category.HARDSHIP,
                reason="Synthetic invalid sponsor", idempotency_key="invalid-sponsor",
                actor=self.operator,
            )
        with self.assertRaisesMessage(Exception, "Sentinel wallet"):
            create_treasury_transfer(
                wallet=self.afri_wallet, amount="100.00", purpose="Invalid source",
                destination_label="Synthetic destination", idempotency_key="invalid-transfer",
                actor=self.operator,
            )

    def test_database_constraints_protect_core_amount_invariants(self):
        item = self.draft()
        with self.assertRaises(IntegrityError), transaction.atomic():
            EncounterSponsorship.objects.filter(pk=item.pk).update(patient_amount=Decimal("1.00"))
        transfer = create_treasury_transfer(
            wallet=self.wallet, amount="100.00", purpose="Synthetic constraint check",
            destination_label="Synthetic destination", idempotency_key="constraint-transfer",
            actor=self.operator,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TreasuryTransfer.objects.filter(pk=transfer.pk).update(amount=Decimal("0.00"))

    def test_approval_requires_genuine_funds_and_separate_checker(self):
        item = submit_encounter_sponsorship(self.draft(), actor=self.operator)
        with self.assertRaisesMessage(Exception, "creator cannot decide"):
            decide_encounter_sponsorship(item, actor=self.operator, approve=True)
        with self.assertRaisesMessage(Exception, "Insufficient genuine Sentinel funds"):
            decide_encounter_sponsorship(item, actor=self.approver, approve=True)
        self.fund()
        approved = decide_encounter_sponsorship(item, actor=self.approver, approve=True)
        self.assertEqual(approved.status, EncounterSponsorship.Status.APPROVED)
        self.assertEqual(self.wallet.available_balance, Decimal("15000.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("10000.00"))

    def test_submission_approval_and_capture_are_idempotent_and_preserve_allocations(self):
        self.fund()
        item = self.draft()
        self.assertEqual(self.draft().pk, item.pk)
        item = submit_encounter_sponsorship(item, actor=self.operator)
        self.assertEqual(submit_encounter_sponsorship(item, actor=self.operator).pk, item.pk)
        item = decide_encounter_sponsorship(item, actor=self.approver, approve=True)
        self.assertEqual(decide_encounter_sponsorship(item, actor=self.approver, approve=True).reservation_id, item.reservation_id)
        item.financial_record.refresh_from_db()
        self.assertFalse(item.financial_record.financially_releasable)
        item = capture_encounter_sponsorship(item, actor=self.operator)
        capture_encounter_sponsorship(item, actor=self.operator)
        record = EncounterFinancialRecord.objects.get(encounter=self.encounter)
        self.assertEqual(item.status, EncounterSponsorship.Status.CAPTURED)
        self.assertEqual(record.payer_type, EncounterFinancialRecord.PayerType.PROGRAMME)
        self.assertEqual(record.payment_method, EncounterFinancialRecord.PaymentMethod.WALLET)
        self.assertTrue(record.financially_releasable)
        self.assertEqual(record.allocations.count(), 2)
        self.assertEqual(WalletLedgerEntry.objects.filter(reservation=item.reservation).count(), 2)
        self.assertEqual(item.events.count(), 4)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.programme, "ocular_diagnostics")
        self.assertFalse(StructuredReport.objects.filter(encounter=self.encounter).exists())

    def test_generic_lifecycle_capture_routes_through_sponsorship_once(self):
        self.fund()
        item = decide_encounter_sponsorship(
            submit_encounter_sponsorship(self.draft(), actor=self.operator),
            actor=self.approver, approve=True,
        )
        reservation = capture_financial_record_wallet_reservation(
            item.financial_record, actor=self.operator
        )
        item.refresh_from_db()
        self.assertEqual(item.status, EncounterSponsorship.Status.CAPTURED)
        self.assertEqual(reservation.pk, item.reservation_id)
        self.assertEqual(
            WalletLedgerEntry.objects.filter(
                reservation=reservation,
                entry_type=WalletLedgerEntry.EntryType.SERVICE_CAPTURE,
            ).count(),
            1,
        )

    def test_service_partner_earning_waits_for_existing_report_condition_and_posts_once(self):
        partner = Organization.objects.create(
            clinic_id="SYNTHETIC-SPONSOR-PARTNER", name="Synthetic Sponsor Partner",
            organization_type="service_partner",
        )
        session = AssessmentServiceSession.objects.create(
            service_date=self.encounter.encounter_date,
            location_type=AssessmentServiceSession.LocationType.CLINIC,
            participating_organization=self.clinic, service_branch=self.branch,
            provider_type=AssessmentServiceSession.ProviderType.SERVICE_PARTNER,
            service_partner=partner, camera_team_rate=Decimal("5000.00"),
            logistics_allocation_rate=Decimal("0.00"), currency="NGN",
            status=AssessmentServiceSession.Status.ACTIVE,
            created_by=self.operator, activated_by=self.operator,
        )
        attach_encounter_to_service_session(self.encounter, session, self.operator)
        self.fund()
        item = capture_encounter_sponsorship(decide_encounter_sponsorship(
            submit_encounter_sponsorship(self.draft(), actor=self.operator),
            actor=self.approver, approve=True,
        ), actor=self.operator)
        self.assertEqual(ServicePartnerEarning.objects.count(), 0)
        StructuredReport.objects.create(
            report_id="SYNTHETIC-SPONSOR-REPORT", encounter=self.encounter,
            patient=self.patient, review_date=date.today(), report_status="clinic_issued",
        )
        first = recognize_service_partner_earning(
            item.financial_record, trigger_source="sponsorship-test"
        )
        second = recognize_service_partner_earning(
            item.financial_record, trigger_source="sponsorship-test-retry"
        )
        self.assertIsNotNone(first)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.amount, Decimal("5000.00"))
        self.assertEqual(ServicePartnerEarning.objects.count(), 1)

    def test_preexisting_paystack_payment_cannot_capture_approved_sponsorship(self):
        self.fund()
        item = self.draft()
        payment = PaymentTransaction.objects.create(
            reference="SYNTHETIC-PREEXISTING-PAYMENT",
            purpose=PaymentTransaction.Purpose.ENCOUNTER_PAYMENT,
            email="synthetic@example.invalid", expected_amount=item.gross_service_value,
            currency="NGN", financial_record=item.financial_record,
        )
        item = decide_encounter_sponsorship(
            submit_encounter_sponsorship(item, actor=self.operator),
            actor=self.approver, approve=True,
        )
        payload = {"status": True, "data": {
            "status": "success", "reference": payment.reference,
            "currency": "NGN", "amount": int(payment.expected_amount * 100),
        }}
        with self.assertRaisesMessage(Exception, "approved Sentinel sponsorship"):
            post_verified_payment(payment, payload)
        payment.refresh_from_db()
        item.financial_record.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.CREATED)
        self.assertFalse(item.financial_record.financially_releasable)
        self.assertFalse(WalletLedgerEntry.objects.filter(
            reservation=item.reservation,
            entry_type=WalletLedgerEntry.EntryType.SERVICE_CAPTURE,
        ).exists())

    def test_cancellation_releases_approved_reservation_before_capture(self):
        self.fund()
        item = decide_encounter_sponsorship(
            submit_encounter_sponsorship(self.draft(), actor=self.operator),
            actor=self.approver, approve=True,
        )
        cancelled = cancel_encounter_sponsorship(item, actor=self.operator, reason="Synthetic cancellation")
        cancel_encounter_sponsorship(cancelled, actor=self.operator, reason="Repeated cancellation")
        self.assertEqual(cancelled.status, EncounterSponsorship.Status.CANCELLED)
        self.assertEqual(self.wallet.available_balance, Decimal("25000.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(cancelled.events.filter(action="cancelled").count(), 1)

    def test_captured_sponsorship_uses_append_only_controlled_correction(self):
        self.fund()
        item = capture_encounter_sponsorship(decide_encounter_sponsorship(
            submit_encounter_sponsorship(self.draft(), actor=self.operator),
            actor=self.approver, approve=True,
        ), actor=self.operator)
        capture_entry = item.reservation.ledger_entries.get(
            entry_type=WalletLedgerEntry.EntryType.SERVICE_CAPTURE
        )
        original_count = WalletLedgerEntry.objects.count()
        correction = create_finance_action_request(
            action_type=FinanceActionRequest.ActionType.REFUND,
            wallet=self.wallet, amount=item.gross_service_value,
            reason="Approved synthetic sponsorship correction",
            external_reference="SYNTHETIC-CORRECTION",
            idempotency_key="sponsorship-correction", requested_by=self.operator,
            financial_record=item.financial_record, related_entry=capture_entry,
        )
        approved = approve_finance_action_request(correction, decided_by=self.approver)
        self.assertEqual(approved.posted_entry.related_entry_id, capture_entry.id)
        self.assertEqual(WalletLedgerEntry.objects.count(), original_count + 1)
        self.assertTrue(WalletLedgerEntry.objects.filter(pk=capture_entry.pk).exists())
        self.assertEqual(
            approve_finance_action_request(correction, decided_by=self.approver).posted_entry_id,
            approved.posted_entry_id,
        )

    def test_transferable_surplus_excludes_afri_and_approved_transfer_commitment(self):
        self.fund()
        top_up_wallet(self.afri_wallet, "90000.00", "afri-not-sentinel")
        transfer = create_treasury_transfer(
            wallet=self.wallet, amount="5000.00", purpose="Synthetic approved transfer",
            destination_label="Controlled synthetic destination", idempotency_key="transfer-one",
            actor=self.operator,
        )
        transfer = submit_treasury_transfer(transfer, actor=self.operator)
        transfer = decide_treasury_transfer(transfer, actor=self.approver, approve=True)
        summary = sentinel_treasury_summary()
        self.assertEqual(summary["available"], Decimal("25000.00"))
        self.assertEqual(summary["transferable_surplus"], Decimal("20000.00"))
        evidence = SimpleUploadedFile("evidence.pdf", b"synthetic evidence", content_type="application/pdf")
        transfer = record_treasury_transfer_execution(
            transfer, actor=self.operator, external_reference="SYNTHETIC-EXECUTION", evidence=evidence
        )
        repeated = record_treasury_transfer_execution(
            transfer, actor=self.operator, external_reference="IGNORED-RETRY", evidence=evidence
        )
        self.assertEqual(repeated.ledger_entry_id, transfer.ledger_entry_id)
        self.assertEqual(transfer.events.filter(action="execution_recorded").count(), 1)
        self.assertEqual(sentinel_treasury_summary()["transferable_surplus"], Decimal("20000.00"))
        reversed_transfer = reverse_treasury_transfer(transfer, actor=self.approver, reason="Synthetic correction")
        reverse_treasury_transfer(reversed_transfer, actor=self.approver, reason="Repeated reversal")
        self.assertEqual(reversed_transfer.events.filter(action="reversed").count(), 1)
        self.assertEqual(self.wallet.available_balance, Decimal("25000.00"))

    def test_transfer_approval_enforces_checker_and_wallet_specific_surplus(self):
        self.fund("5000.00")
        transfer = submit_treasury_transfer(create_treasury_transfer(
            wallet=self.wallet, amount="5001.00", purpose="Synthetic excess",
            destination_label="Controlled synthetic destination", idempotency_key="transfer-excess",
            actor=self.operator,
        ), actor=self.operator)
        with self.assertRaisesMessage(Exception, "creator cannot decide"):
            decide_treasury_transfer(transfer, actor=self.operator, approve=True)
        with self.assertRaisesMessage(Exception, "transferable surplus"):
            decide_treasury_transfer(transfer, actor=self.approver, approve=True)

    def test_approved_transfer_cancellation_is_idempotent_and_posts_no_ledger(self):
        self.fund("5000.00")
        transfer = decide_treasury_transfer(submit_treasury_transfer(create_treasury_transfer(
            wallet=self.wallet, amount="1000.00", purpose="Synthetic cancellation",
            destination_label="Controlled synthetic destination", idempotency_key="transfer-cancel",
            actor=self.operator,
        ), actor=self.operator), actor=self.approver, approve=True)
        ledger_count = WalletLedgerEntry.objects.count()
        cancelled = cancel_treasury_transfer(
            transfer, actor=self.operator, reason="No longer required"
        )
        cancel_treasury_transfer(cancelled, actor=self.operator, reason="Repeated request")
        self.assertEqual(cancelled.events.filter(action="cancelled").count(), 1)
        self.assertEqual(WalletLedgerEntry.objects.count(), ledger_count)
        self.assertEqual(sentinel_treasury_summary()["transferable_surplus"], Decimal("5000.00"))

    def test_stale_approval_requests_fail_without_overwriting_terminal_state(self):
        sponsorship = submit_encounter_sponsorship(self.draft(), actor=self.operator)
        sponsorship = cancel_encounter_sponsorship(
            sponsorship, actor=self.operator, reason="Synthetic stale-state test"
        )
        client = APIClient()
        client.force_authenticate(self.approver)
        response = client.post(f"/api/finance/sponsorships/{sponsorship.pk}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        sponsorship.refresh_from_db()
        self.assertEqual(sponsorship.status, EncounterSponsorship.Status.CANCELLED)

        transfer = submit_treasury_transfer(create_treasury_transfer(
            wallet=self.wallet, amount="100.00", purpose="Synthetic stale transfer",
            destination_label="Synthetic destination", idempotency_key="stale-transfer",
            actor=self.operator,
        ), actor=self.operator)
        transfer = cancel_treasury_transfer(
            transfer, actor=self.operator, reason="Synthetic stale-state test"
        )
        response = client.post(f"/api/finance/treasury-transfers/{transfer.pk}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, TreasuryTransfer.Status.CANCELLED)

    def test_api_mutation_requires_exact_internal_role_and_superuser_alone_is_denied(self):
        superuser = self.user("sponsor-superuser", "", internal=True, superuser=True)
        unmarked_operator = self.user("unmarked-sponsor-operator", "finance_operator", internal=False)
        client = APIClient()
        payload = {
            "encounter": self.encounter.pk, "sponsor_wallet": self.wallet.pk,
            "category": EncounterSponsorship.Category.HARDSHIP,
            "reason": "Synthetic hardship", "idempotency_key": "api-superuser-denied",
        }
        client.force_authenticate(superuser)
        self.assertEqual(client.post("/api/finance/sponsorships/", payload, format="json").status_code, 403)
        self.assertEqual(client.post("/api/finance/treasury-transfers/", {
            "wallet": self.wallet.pk, "amount": "100.00", "purpose": "Denied",
            "destination_label": "Denied", "idempotency_key": "denied-superuser-transfer",
        }, format="json").status_code, 403)
        client.force_authenticate(unmarked_operator)
        self.assertEqual(client.post("/api/finance/sponsorships/", payload, format="json").status_code, 403)
        client.force_authenticate(self.operator)
        self.assertEqual(client.post("/api/finance/sponsorships/", payload, format="json").status_code, 201)

    def test_treasury_evidence_requires_authenticated_finance_access(self):
        self.fund("5000.00")
        transfer = decide_treasury_transfer(submit_treasury_transfer(create_treasury_transfer(
            wallet=self.wallet, amount="1000.00", purpose="Synthetic evidence access",
            destination_label="Controlled synthetic destination", idempotency_key="evidence-transfer",
            actor=self.operator,
        ), actor=self.operator), actor=self.approver, approve=True)
        evidence = SimpleUploadedFile("evidence.pdf", b"synthetic evidence", content_type="application/pdf")
        transfer = record_treasury_transfer_execution(
            transfer, actor=self.operator, external_reference="SYNTHETIC-EVIDENCE", evidence=evidence
        )
        client = APIClient()
        path = f"/api/finance/treasury-transfers/{transfer.pk}/evidence/"
        self.assertIn(client.get(path).status_code, {401, 403})
        unauthorized = self.user("unauthorized-evidence-user", "", internal=True)
        client.force_authenticate(unauthorized)
        self.assertEqual(client.get(path).status_code, 403)
        client.force_authenticate(self.viewer)
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_dashboard_counts_only_verified_sentinel_sources(self):
        self.fund()
        top_up_wallet(
            self.wallet, "3000.00", "verified-paystack",
            metadata={"provider": "paystack"},
        )
        top_up_wallet(self.wallet, "7000.00", "unverified-manual-top-up")
        top_up_wallet(self.afri_wallet, "99000.00", "afri-top-up")
        BankTransferFundingRequest.objects.create(
            wallet=self.wallet, requested_amount=Decimal("4000.00"),
            status=BankTransferFundingRequest.Status.AWAITING_TRANSFER,
        )
        PaymentTransaction.objects.create(
            reference="SYNTHETIC-PENDING-PAYSTACK",
            purpose=PaymentTransaction.Purpose.WALLET_TOP_UP,
            status=PaymentTransaction.Status.INITIALIZED,
            email="synthetic@example.invalid", expected_amount=Decimal("2000.00"),
            currency="NGN", wallet=self.wallet,
        )
        service_partner = Organization.objects.create(
            clinic_id="SYNTHETIC-DASHBOARD-PARTNER", name="Synthetic Dashboard Partner",
            organization_type="service_partner",
        )
        ServicePartnerSettlementBatch.objects.create(
            service_partner=service_partner, assessment_date=date.today(), currency="NGN",
            status=ServicePartnerSettlementBatch.Status.PAID, assessment_count=2,
            gross_amount=Decimal("10000.00"), final_amount=Decimal("7000.00"),
            prepared_by=self.operator, approved_by=self.approver, paid_by=self.operator,
        )
        client = APIClient()
        client.force_authenticate(self.viewer)
        response = client.get("/api/finance/sentinel-dashboard/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["verified_sentinel_cash_received"], "28000.00")
        self.assertEqual(response.data["available_sentinel_funds"], "35000.00")
        self.assertEqual(response.data["pending_unverified_funding"], "6000.00")
        self.assertEqual(response.data["settled_service_partner_earnings"], "7000.00")
        self.assertEqual(
            list(response.data["sponsorship_commitments"]),
            [value for value, _label in EncounterSponsorship.Status.choices],
        )
        paystack = client.get("/api/finance/sentinel-dashboard/?payment_source=paystack")
        self.assertEqual(paystack.data["verified_sentinel_cash_received"], "3000.00")
        self.assertEqual(paystack.data["pending_unverified_funding"], "2000.00")
        self.assertEqual(paystack.data["available_sentinel_funds"], "35000.00")
        bank = client.get("/api/finance/sentinel-dashboard/?payment_source=bank_transfer")
        self.assertEqual(bank.data["pending_unverified_funding"], "4000.00")
