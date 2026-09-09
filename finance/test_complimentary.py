from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from . import test_sponsorship_dashboard as fixtures
from .models import (ComplimentaryRequest, EncounterFinancialRecord, WalletLedgerEntry,
                     WalletReservation, EncounterAllocation, FinancialAuditLog)
from .complimentary import request_complimentary, decide_complimentary
from .services import (price_encounter, sync_encounter_finance_lifecycle,
                       earn_financial_record_allocations, reserve_wallet_funds,
                       approve_financial_record_credit, cancel_financial_record,
                       cancel_encounter_sponsorship)


class ComplimentaryTests(TestCase):
    setUp = fixtures.SponsorshipAndTreasuryTests.setUp
    tearDown = fixtures.SponsorshipAndTreasuryTests.tearDown
    user = fixtures.SponsorshipAndTreasuryTests.user
    draft = fixtures.SponsorshipAndTreasuryTests.draft
    fund = fixtures.SponsorshipAndTreasuryTests.fund

    def prepare(self):
        # Existing cancelled sponsorship with released/no funding is compatible.
        sponsor = self.draft()
        cancel_encounter_sponsorship(sponsor, actor=self.operator, reason="Non-cash service")
        record = sponsor.financial_record
        rule = record.pricing_rule
        rule.gross_amount = Decimal("15000.00")
        rule.save()
        rules = list(rule.allocation_rules.order_by("pk"))
        for index, allocation_rule in enumerate(rules):
            allocation_rule.fixed_amount = Decimal("11000" if index == 0 else "4000")
            allocation_rule.beneficiary_organization = self.sentinel
            allocation_rule.save()
        record = price_encounter(self.encounter, actor=self.operator, force=True, contract_override=record.contract)
        return record

    def request(self, record):
        return request_complimentary(financial_record=record, actor=self.operator,
                                     reason="Approved free service", idempotency_key="noncash-one")

    def test_approval_retains_15000_and_posts_nothing_even_on_retries(self):
        self.fund("97140.00")
        record = self.prepare()
        before = (WalletLedgerEntry.objects.count(), WalletReservation.objects.count(), EncounterFinancialRecord.objects.count())
        allocations = list(record.allocations.values_list("pk", flat=True))
        item = self.request(record)
        self.assertEqual(self.request(record).pk, item.pk)
        for _ in range(2):
            item = decide_complimentary(item, actor=self.approver, action="approve")
            sync_encounter_finance_lifecycle(self.encounter)
            earn_financial_record_allocations(record)
        record.refresh_from_db()
        self.assertEqual(record.gross_amount, Decimal("15000.00"))
        self.assertEqual(record.outstanding_amount, 0)
        self.assertEqual(record.allocated_amount, 0)
        self.assertTrue(record.financially_releasable)
        self.assertIsNone(record.captured_at)
        self.assertIsNone(record.payer_organization_id)
        self.assertEqual(record.disposition, "complimentary_non_cash")
        self.assertEqual(before, (WalletLedgerEntry.objects.count(), WalletReservation.objects.count(), EncounterFinancialRecord.objects.count()))
        self.assertEqual(list(record.allocations.values_list("pk", flat=True)), allocations)
        self.assertFalse(record.allocations.exclude(status="reversed").exists())
        self.assertEqual(item.events.count(), 2)
        self.assertEqual(FinancialAuditLog.objects.filter(financial_record=record, action="complimentary_approved").count(), 1)
        self.assertEqual(self.wallet.available_balance, Decimal("97140.00"))
        self.assertEqual(self.wallet.reserved_balance, 0)

    def test_role_and_maker_checker_controls_include_retries(self):
        record = self.prepare()
        item = self.request(record)
        self.operator.groups.add(self.approver.groups.first())
        with self.assertRaises(ValidationError):
            decide_complimentary(item, actor=self.operator, action="approve")
        outsider = self.user("root-only", "", superuser=True)
        with self.assertRaises(PermissionDenied):
            decide_complimentary(item, actor=outsider, action="approve")
        for role in ["clinic_admin", "clinic_owner_optometrist", "optometrist", "finance_approver"]:
            user = self.user("denied-" + role, role)
            client = APIClient(); client.force_authenticate(user)
            self.assertEqual(client.get("/api/finance/complimentary/").status_code, 403)
            self.assertEqual(client.post(f"/api/finance/complimentary/{item.pk}/approve/").status_code, 403)

    def test_approved_complimentary_is_excluded_from_settlement(self):
        from datetime import date
        from .services import create_settlement_batch
        from .models import SettlementItem
        self.fund("97140")
        record = self.prepare()
        before = (WalletLedgerEntry.objects.count(), self.wallet.available_balance, self.wallet.reserved_balance)
        decide_complimentary(self.request(record), actor=self.approver, action="approve")
        with self.assertRaisesMessage(ValidationError, "No unsettled allocations"):
            create_settlement_batch(self.sentinel, date.today(), date.today(), actor=self.operator)
        self.assertFalse(SettlementItem.objects.filter(allocation__financial_record=record).exists())
        self.assertEqual(before, (WalletLedgerEntry.objects.count(), self.wallet.available_balance, self.wallet.reserved_balance))
        record.refresh_from_db()
        self.assertEqual(record.gross_amount, Decimal("15000"))

    def test_no_reprice_reserve_credit_or_cancel_after_approval(self):
        record = self.prepare()
        decide_complimentary(self.request(record), actor=self.approver, action="approve")
        for operation in [
            lambda: price_encounter(self.encounter, force=True),
            lambda: reserve_wallet_funds(self.wallet, record, "15000", "forbidden"),
            lambda: approve_financial_record_credit(record),
            lambda: cancel_financial_record(record),
        ]:
            with self.assertRaises(ValidationError):
                operation()
        self.assertEqual(WalletLedgerEntry.objects.count(), 0)

    def test_pending_cancel_reject_are_audited_and_no_finance_effect(self):
        record = self.prepare()
        for action in ["cancel", "reject"]:
            item = request_complimentary(financial_record=record, actor=self.operator, reason="Free", idempotency_key=action)
            actor = self.operator if action == "cancel" else self.approver
            for _ in range(2):
                item = decide_complimentary(item, actor=actor, action=action, reason="Correction")
            self.assertEqual(item.events.count(), 2)
        record.refresh_from_db()
        self.assertEqual(record.outstanding_amount, Decimal("15000"))
        self.assertFalse(record.financially_releasable)
        self.assertEqual(WalletLedgerEntry.objects.count(), 0)

    def test_reject_changed_pricing_external_allocation_and_paid_record(self):
        record = self.prepare(); item = self.request(record)
        record.gross_amount = Decimal("16000"); record.save()
        with self.assertRaises(ValidationError):
            decide_complimentary(item, actor=self.approver, action="approve")
        record.gross_amount = Decimal("15000"); record.save()
        record.allocations.update(beneficiary_organization=self.afri)
        with self.assertRaises(ValidationError):
            decide_complimentary(item, actor=self.approver, action="approve")
        record.status = "captured"; record.save()
        with self.assertRaises(ValidationError):
            decide_complimentary(item, actor=self.approver, action="approve")

    def test_api_and_immutable_audit(self):
        record = self.prepare(); client = APIClient(); client.force_authenticate(self.operator)
        response = client.post("/api/finance/complimentary/", {"financial_record": record.pk, "reason": "Free", "idempotency_key": "api"})
        self.assertEqual(response.status_code, 201, response.data)
        client.force_authenticate(self.approver)
        response = client.post(f"/api/finance/complimentary/{response.data['id']}/approve/")
        self.assertEqual(response.status_code, 200, response.data)
        event = ComplimentaryRequest.objects.get(pk=response.data["id"]).events.first()
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()

    def test_cancelled_reserved_sponsorship_allows_non_cash_without_new_entries(self):
        from .services import submit_encounter_sponsorship, decide_encounter_sponsorship
        self.fund()
        sponsor = self.draft()
        submit_encounter_sponsorship(sponsor, actor=self.operator)
        decide_encounter_sponsorship(sponsor, actor=self.approver, approve=True)
        cancel_encounter_sponsorship(sponsor, actor=self.operator, reason="Non-cash classification")
        record = sponsor.financial_record
        record.refresh_from_db()
        before = (WalletLedgerEntry.objects.count(), WalletReservation.objects.count(), self.wallet.available_balance)
        item = self.request(record)
        decide_complimentary(item, actor=self.approver, action="approve")
        self.assertEqual(before, (WalletLedgerEntry.objects.count(), WalletReservation.objects.count(), self.wallet.available_balance))

    def test_active_sponsorship_and_idempotency_conflicts_rejected(self):
        sponsor = self.draft()
        with self.assertRaises(ValidationError):
            self.request(sponsor.financial_record)
        cancel_encounter_sponsorship(sponsor, actor=self.operator, reason="Cancel")
        item = self.request(sponsor.financial_record)
        with self.assertRaises(ValidationError):
            request_complimentary(financial_record=sponsor.financial_record, actor=self.operator,
                reason="Changed intent", idempotency_key=item.idempotency_key)

    def test_database_rejects_cash_state_for_complimentary(self):
        from django.db import IntegrityError, transaction
        record = self.prepare()
        decide_complimentary(self.request(record), actor=self.approver, action="approve")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EncounterFinancialRecord.objects.filter(pk=record.pk).update(outstanding_amount=Decimal("1"))
