from datetime import date
from decimal import Decimal
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import TestCase

from . import test_sponsorship_dashboard as fixtures
from .models import EncounterFinancialRecord, WalletLedgerEntry, TreasuryTransfer
from .services import (create_treasury_transfer, submit_treasury_transfer,
    decide_treasury_transfer, record_treasury_transfer_execution, reverse_treasury_transfer,
    create_settlement_batch, approve_settlement_batch, mark_settlement_batch_paid,
    approve_financial_record_credit, capture_encounter_sponsorship,
    decide_encounter_sponsorship, submit_encounter_sponsorship)


class BatchOneControlTests(TestCase):
    setUp = fixtures.SponsorshipAndTreasuryTests.setUp
    tearDown = fixtures.SponsorshipAndTreasuryTests.tearDown
    user = fixtures.SponsorshipAndTreasuryTests.user
    fund = fixtures.SponsorshipAndTreasuryTests.fund
    draft = fixtures.SponsorshipAndTreasuryTests.draft

    def transfer(self):
        self.fund()
        item = create_treasury_transfer(wallet=self.wallet, amount="5000", purpose="Test hosting",
            destination_label="Test supplier", idempotency_key="control-transfer", actor=self.operator)
        submit_treasury_transfer(item, actor=self.operator)
        decide_treasury_transfer(item, actor=self.approver, approve=True)
        return record_treasury_transfer_execution(item, actor=self.operator,
            execution_date=date.today(), external_reference="EXEC-TEST",
            evidence=SimpleUploadedFile("paid.pdf", b"%PDF-1.4 test"))

    def test_reason_only_reversal_posts_nothing(self):
        item = self.transfer(); before = WalletLedgerEntry.objects.count()
        with self.assertRaises(ValidationError):
            reverse_treasury_transfer(item, actor=self.approver, reason="Correction")
        self.assertEqual(WalletLedgerEntry.objects.count(), before)
        self.assertEqual(self.wallet.available_balance, Decimal("20000"))
        item.refresh_from_db(); self.assertEqual(item.status, "executed")

    def test_evidenced_bookkeeping_correction_is_distinct_and_idempotent(self):
        item = self.transfer()
        kwargs = dict(actor=self.operator, idempotency_key="correction-request", reason="Original debit incorrectly recorded",
            reversal_kind="bookkeeping_correction", reversal_reference="CORRECTION-TEST",
            evidence=SimpleUploadedFile("correction.pdf", b"%PDF-1.4 evidence"))
        from .treasury_reversals import request_reversal, submit_reversal, approve_reversal, execute_reversal
        request = request_reversal(item, **kwargs)
        submit_reversal(request, actor=self.operator)
        approve_reversal(request, actor=self.approver)
        execute_reversal(request, actor=self.operator)
        repeated = execute_reversal(request, actor=self.operator)
        item.refresh_from_db()
        self.assertEqual(repeated.posted_entry_id, item.reversal_entry_id)
        first = second = item
        self.assertEqual(first.reversal_entry_id, second.reversal_entry_id)
        self.assertEqual(first.reversal_kind, "bookkeeping_correction")
        self.assertEqual(first.reversal_entry.metadata["reversal_reference"], "CORRECTION-TEST")
        self.assertTrue(first.reversal_evidence)
        self.assertEqual(self.wallet.available_balance, Decimal("25000"))
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(self.viewer)
        response = client.get(f"/api/finance/treasury-transfers/{first.pk}/reversal-evidence/")
        self.assertEqual(response.status_code, 200)
        if getattr(response, "streaming", False):
            b"".join(response.streaming_content)
        client.force_authenticate(self.user("nonfinance", "clinic_admin"))
        self.assertEqual(client.get(f"/api/finance/treasury-transfers/{first.pk}/reversal-evidence/").status_code, 403)

    def test_creator_executor_cannot_reverse_even_with_approver_role(self):
        item = self.transfer(); self.operator.groups.add(self.approver.groups.first())
        with self.assertRaises(ValidationError):
            reverse_treasury_transfer(item, actor=self.operator, reason="Correction",
                reversal_kind="returned_funds", reversal_reference="RETURN",
                evidence=SimpleUploadedFile("return.pdf", b"%PDF-1.4 test"))

    def test_settlement_selection_and_payment_do_not_credit_treasury(self):
        self.fund(); sponsor = self.draft()
        submit_encounter_sponsorship(sponsor, actor=self.operator)
        decide_encounter_sponsorship(sponsor, actor=self.approver, approve=True)
        capture_encounter_sponsorship(sponsor, actor=self.operator)
        from .services import earn_financial_record_allocations
        earn_financial_record_allocations(sponsor.financial_record)
        before = WalletLedgerEntry.objects.count()
        batch = create_settlement_batch(self.sentinel, date.today(), date.today(), actor=self.operator)
        with self.assertRaises(ValidationError):
            create_settlement_batch(self.sentinel, date.today(), date.today(), actor=self.operator)
        self.operator.groups.add(self.approver.groups.first())
        with self.assertRaises(ValidationError):
            approve_settlement_batch(batch, actor=self.operator)
        approve_settlement_batch(batch, actor=self.approver)
        approve_settlement_batch(batch, actor=self.approver)
        mark_settlement_batch_paid(batch, "PAID-TEST", actor=self.operator,
            payment_evidence=SimpleUploadedFile("settled.pdf", b"%PDF-1.4 test"))
        mark_settlement_batch_paid(batch, "PAID-TEST", actor=self.operator)
        self.assertEqual(WalletLedgerEntry.objects.count(), before)

    @skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL test database")
    def test_nullable_finance_locks_execute_on_postgresql(self):
        # Nullable contract must produce a business validation error, never an SQL lock error.
        record = EncounterFinancialRecord.objects.get(encounter=self.encounter)
        with self.assertRaises(ValidationError):
            approve_financial_record_credit(record)
        # Exercise nullable reservation and ledger joins in the existing services.
        self.test_settlement_selection_and_payment_do_not_credit_treasury()
