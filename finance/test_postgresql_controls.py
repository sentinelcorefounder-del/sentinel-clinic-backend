"""Execute only on a disposable PostgreSQL database using Django's test runner."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from . import test_finance_hardening as hardening
from . import test_complimentary as complimentary
from . import tests as legacy
from . import test_internal_foundation as foundation
from .models import EncounterFinancialRecord, SettlementItem, WalletLedgerEntry
from .services import (submit_encounter_sponsorship, decide_encounter_sponsorship,
    capture_encounter_sponsorship, cancel_encounter_sponsorship,
    capture_financial_record_wallet_reservation, approve_financial_record_credit,
    reserve_service_allowance)


@skipUnless(connection.vendor == "postgresql", "Requires an isolated PostgreSQL test database")
class PostgreSQLFinanceLocks(TransactionTestCase):
    setUp = hardening.FinanceHardeningTests.setUp
    tearDown = hardening.FinanceHardeningTests.tearDown
    user = hardening.FinanceHardeningTests.user
    fund = hardening.FinanceHardeningTests.fund
    draft = hardening.FinanceHardeningTests.draft
    transfer = hardening.FinanceHardeningTests.transfer
    reversal = hardening.FinanceHardeningTests.reversal
    earned = hardening.FinanceHardeningTests.earned
    batch = hardening.FinanceHardeningTests.batch
    prepare = complimentary.ComplimentaryTests.prepare
    request = complimentary.ComplimentaryTests.request

    test_complimentary_request_approval_settlement = complimentary.ComplimentaryTests.test_approved_complimentary_is_excluded_from_settlement
    test_treasury_reversal_locks = hardening.FinanceHardeningTests.test_reversal_posts_only_at_execution_and_is_idempotent
    test_settlement_creation_approval_payment = hardening.FinanceHardeningTests.test_database_membership_unique_and_cancelled_history_preserved

    def test_settlement_payment_locks(self):
        from datetime import date
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .services import approve_settlement_batch, mark_settlement_batch_paid
        self.earned()
        batch = self.batch()
        approve_settlement_batch(batch, actor=self.approver)
        mark_settlement_batch_paid(batch, "PG-PAY", actor=self.operator,
            payment_evidence=SimpleUploadedFile("paid.pdf", b"paid"))

    def test_sponsorship_nullable_reservation_cancellation(self):
        item = self.draft()
        self.assertIsNone(item.reservation_id)
        cancel_encounter_sponsorship(item, actor=self.operator, reason="Draft cancellation")

    def test_sponsorship_approval_and_cancellation_release(self):
        self.fund()
        item = self.draft()
        submit_encounter_sponsorship(item, actor=self.operator)
        decide_encounter_sponsorship(item, actor=self.approver, approve=True)
        cancel_encounter_sponsorship(item, actor=self.operator, reason="Release genuine funds")
        self.assertEqual(self.wallet.available_balance, 25000)
        self.assertEqual(self.wallet.reserved_balance, 0)

    def test_nullable_credit_and_allowance_joins(self):
        record = EncounterFinancialRecord.objects.get(encounter=self.encounter)
        self.assertIsNone(record.contract_id)
        for operation in [approve_financial_record_credit, reserve_service_allowance]:
            # Any SQL NotSupportedError/OperationalError escapes this assertion.
            with self.assertRaises(ValidationError):
                operation(record, actor=self.operator)

    def race(self, operations):
        barrier = Barrier(len(operations))
        def run(operation):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                barrier.wait(timeout=10)
                try:
                    operation()
                    return "ok"
                except ValidationError:
                    return "business_rejection"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(run, operations))

    def test_concurrent_settlement_preparation_has_one_membership(self):
        self.earned()
        results = self.race([self.batch, self.batch])
        self.assertCountEqual(results, ["ok", "business_rejection"])
        self.assertEqual(SettlementItem.objects.count(), 1)

    def test_direct_and_generic_capture_use_same_lock_order(self):
        self.fund()
        item = self.draft()
        submit_encounter_sponsorship(item, actor=self.operator)
        item = decide_encounter_sponsorship(item, actor=self.approver, approve=True)
        results = self.race([lambda: capture_encounter_sponsorship(item, actor=self.operator),
            lambda: capture_financial_record_wallet_reservation(item.financial_record, actor=self.operator)])
        self.assertIn("ok", results)
        item.refresh_from_db()
        self.assertEqual(item.status, "captured")
        self.assertEqual(WalletLedgerEntry.objects.filter(entry_type="service_capture").count(), 1)

    def test_concurrent_reversal_execution_posts_once(self):
        from .treasury_reversals import submit_reversal, approve_reversal, execute_reversal
        item = self.reversal(self.transfer())
        submit_reversal(item, actor=self.operator)
        approve_reversal(item, actor=self.approver)
        results = self.race([lambda: execute_reversal(item, actor=self.operator)] * 2)
        self.assertEqual(results, ["ok", "ok"])
        self.assertEqual(WalletLedgerEntry.objects.filter(entry_type="reversal").count(), 1)
        self.assertEqual(self.wallet.available_balance, 25000)


@skipUnless(connection.vendor == "postgresql", "Requires an isolated PostgreSQL test database")
class PostgreSQLAllowanceLocks(TransactionTestCase):
    setUp = legacy.ServiceAllowanceTests.setUp
    test_allowance_reservation = legacy.ServiceAllowanceTests.test_allowance_reservation_never_credits_or_releases
    test_allowance_funding_capture = legacy.ServiceAllowanceTests.test_real_funding_replaces_allowance_then_capture_can_proceed


@skipUnless(connection.vendor == "postgresql", "Requires an isolated PostgreSQL test database")
class PostgreSQLSessionLocks(TransactionTestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        for name in ["finance_admin", "finance_operator", "finance_approver"]:
            Group.objects.get_or_create(name=name)
        foundation.InternalFinanceFoundationTests.setUp(self)

    session = foundation.InternalFinanceFoundationTests.session
    encounter = foundation.InternalFinanceFoundationTests.encounter
    test_nullable_branch_attachment = foundation.InternalFinanceFoundationTests.test_attachment_requires_exact_branch_and_branchless_session

    def test_nullable_partner_attachment(self):
        from .services import attach_encounter_to_service_session
        session = self.session(status="active", provider_type="sentinel", service_partner=None)
        attached = attach_encounter_to_service_session(self.encounter(), session, self.admin)
        self.assertEqual(attached.service_session_id, session.pk)
