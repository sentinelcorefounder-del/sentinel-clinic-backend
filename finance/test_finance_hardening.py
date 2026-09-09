from datetime import date

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from . import test_batch1_controls as fixtures
from .models import FinanceActionRequest, FinanceControlAudit, SettlementItem, WalletLedgerEntry
from .services import (create_settlement_batch, approve_settlement_batch, mark_settlement_batch_paid,
    cancel_settlement_batch, submit_encounter_sponsorship, decide_encounter_sponsorship,
    capture_encounter_sponsorship, earn_financial_record_allocations, create_finance_action_request)
from .treasury_reversals import (request_reversal, submit_reversal, approve_reversal,
    reject_reversal, cancel_reversal, execute_reversal)


class FinanceHardeningTests(TestCase):
    setUp = fixtures.BatchOneControlTests.setUp
    tearDown = fixtures.BatchOneControlTests.tearDown
    user = fixtures.BatchOneControlTests.user
    fund = fixtures.BatchOneControlTests.fund
    draft = fixtures.BatchOneControlTests.draft
    transfer = fixtures.BatchOneControlTests.transfer

    def reversal(self, transfer, **changes):
        kwargs = dict(actor=self.operator, reversal_kind="returned_funds", reversal_reference="RETURN-001",
            reason="Receipt of full refund", evidence=SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 receipt"),
            idempotency_key="reversal-001")
        kwargs.update(changes)
        return request_reversal(transfer, **kwargs)

    def earned(self):
        self.fund()
        sponsor = self.draft()
        submit_encounter_sponsorship(sponsor, actor=self.operator)
        decide_encounter_sponsorship(sponsor, actor=self.approver, approve=True)
        capture_encounter_sponsorship(sponsor, actor=self.operator)
        earn_financial_record_allocations(sponsor.financial_record)

    def batch(self):
        return create_settlement_batch(self.sentinel, date.today(), date.today(), actor=self.operator)

    def outsiders(self):
        return [None, self.user("random", ""), self.user("root", "", superuser=True),
            self.user("clinic-admin", "clinic_admin"), self.user("optometrist", "optometrist"),
            self.user("external-finance", "finance_approver")]

    def test_settlement_service_authority_all_actions(self):
        self.earned()
        batch = self.batch()
        for actor in self.outsiders():
            for operation in [lambda: create_settlement_batch(self.sentinel, date.today(), date.today(), actor=actor),
                lambda: approve_settlement_batch(batch, actor=actor),
                lambda: mark_settlement_batch_paid(batch, "PAY", actor=actor),
                lambda: cancel_settlement_batch(batch, "Cancelled", actor=actor)]:
                with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                    operation()
        with self.assertRaises(PermissionDenied):
            approve_settlement_batch(batch, actor=self.operator)
        for operation in [lambda: self.batch_with_actor(self.approver),
                lambda: cancel_settlement_batch(batch, "No", actor=self.approver),
                lambda: mark_settlement_batch_paid(batch, "PAY", actor=self.approver)]:
            with self.assertRaises(PermissionDenied):
                operation()
        self.operator.groups.add(self.approver.groups.first())
        with self.assertRaises(ValidationError):
            approve_settlement_batch(batch, actor=self.operator)
        approved = approve_settlement_batch(batch, actor=self.approver)
        self.assertEqual(approve_settlement_batch(batch, actor=self.approver).approved_at, approved.approved_at)
        before = WalletLedgerEntry.objects.count()
        paid = mark_settlement_batch_paid(batch, "PAY", actor=self.operator,
            payment_evidence=SimpleUploadedFile("pay.pdf", b"paid"))
        self.assertEqual(mark_settlement_batch_paid(batch, "PAY", actor=self.operator).paid_at, paid.paid_at)
        self.assertEqual(before, WalletLedgerEntry.objects.count())
        with self.assertRaises(ValidationError):
            mark_settlement_batch_paid(batch, "DIFFERENT", actor=self.operator)

    def batch_with_actor(self, actor):
        return create_settlement_batch(self.sentinel, date.today(), date.today(), actor=actor)

    def test_database_membership_unique_and_cancelled_history_preserved(self):
        self.earned()
        batch = self.batch()
        item = batch.items.first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SettlementItem.objects.create(batch=batch, allocation=item.allocation, amount=item.amount, currency=item.currency)
        with self.assertRaises(ValidationError):
            self.batch()
        cancel_settlement_batch(batch, "Replace payment instructions", actor=self.operator)
        replacement = self.batch()
        self.assertTrue(batch.items.filter(allocation=item.allocation).exists())
        self.assertTrue(replacement.items.filter(allocation=item.allocation).exists())
        with self.assertRaises(ValidationError):
            self.batch()

    def test_reversal_posts_only_at_execution_and_is_idempotent(self):
        transfer = self.transfer()
        before = WalletLedgerEntry.objects.count()
        item = self.reversal(transfer)
        self.assertEqual(self.reversal(transfer).pk, item.pk)
        submit_reversal(item, actor=self.operator)
        submit_reversal(item, actor=self.operator)
        item = approve_reversal(item, actor=self.approver)
        again = approve_reversal(item, actor=self.approver)
        self.assertEqual(item.approved_snapshot, again.approved_snapshot)
        self.assertEqual(WalletLedgerEntry.objects.count(), before)
        self.assertEqual(self.wallet.available_balance, 20000)
        item = execute_reversal(item, actor=self.operator)
        again = execute_reversal(item, actor=self.operator)
        self.assertEqual(item.posted_entry_id, again.posted_entry_id)
        self.assertEqual(WalletLedgerEntry.objects.count(), before + 1)
        self.assertEqual(self.wallet.available_balance, 25000)
        self.assertEqual(FinanceControlAudit.objects.filter(action_request=item).count(), 4)
        self.assertEqual(transfer.events.filter(action="reversed").count(), 1)
        self.assertEqual(item.approved_snapshot, item.posted_entry.metadata["approved_snapshot"])

    def test_reversal_authority_and_independent_checker(self):
        transfer = self.transfer()
        item = self.reversal(transfer)
        for actor in self.outsiders():
            for operation in [lambda: self.reversal(transfer, actor=actor), lambda: submit_reversal(item, actor=actor),
                lambda: approve_reversal(item, actor=actor), lambda: reject_reversal(item, actor=actor, reason="No"),
                lambda: cancel_reversal(item, actor=actor, reason="No"), lambda: execute_reversal(item, actor=actor)]:
                with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                    operation()
        with self.assertRaises(ValidationError):
            execute_reversal(item, actor=self.operator)
        submit_reversal(item, actor=self.operator)
        self.operator.groups.add(self.approver.groups.first())
        with self.assertRaises(ValidationError):
            approve_reversal(item, actor=self.operator)
        with self.assertRaises(ValidationError):
            reject_reversal(item, actor=self.operator, reason="No")
        approve_reversal(item, actor=self.approver)
        self.approver.groups.add(self.operator.groups.get(name="finance_operator"))
        with self.assertRaises(ValidationError):
            execute_reversal(item, actor=self.approver)

    def test_reversal_requires_complete_evidence_and_independent_reference(self):
        transfer = self.transfer()
        for changes in [{"evidence": None}, {"reason": ""}, {"reversal_kind": ""},
                {"reversal_reference": ""}, {"reversal_reference": transfer.external_reference}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.reversal(transfer, **changes)
        self.assertEqual(self.wallet.available_balance, 20000)
        self.assertFalse(FinanceActionRequest.objects.exists())

    def test_original_transfer_maker_cannot_check_another_operators_request(self):
        transfer = self.transfer()
        other = self.user("other-operator", "finance_operator", internal=True)
        item = self.reversal(transfer, actor=other)
        submit_reversal(item, actor=other)
        self.operator.groups.add(self.approver.groups.first())
        with self.assertRaisesMessage(ValidationError, "independent checker"):
            approve_reversal(item, actor=self.operator)
        self.assertEqual(self.wallet.available_balance, 20000)

    def test_active_request_and_terminal_state_cannot_be_duplicated(self):
        transfer = self.transfer()
        item = self.reversal(transfer)
        with self.assertRaises(ValidationError):
            self.reversal(transfer, idempotency_key="second-request")
        submit_reversal(item, actor=self.operator)
        approve_reversal(item, actor=self.approver)
        item = execute_reversal(item, actor=self.operator)
        item.status = "authorized"
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(ValidationError):
            self.reversal(transfer, idempotency_key="after-execution")
        self.assertEqual(self.wallet.available_balance, 25000)

    def test_approval_snapshot_and_evidence_cannot_change(self):
        transfer = self.transfer()
        item = self.reversal(transfer)
        submit_reversal(item, actor=self.operator)
        item = approve_reversal(item, actor=self.approver)
        item.external_reference = "TAMPER"
        with self.assertRaises(ValidationError):
            item.save()
        item.refresh_from_db()
        item.approved_snapshot = {}
        with self.assertRaises(ValidationError):
            item.save()
        item.refresh_from_db()
        with item.evidence.storage.open(item.evidence.name, "wb") as file:
            file.write(b"Changed after approval")
        with self.assertRaisesMessage(ValidationError, "evidence changed"):
            execute_reversal(item, actor=self.operator)
        self.assertEqual(self.wallet.available_balance, 20000)

    def test_reject_cancel_and_replacement_never_post(self):
        transfer = self.transfer()
        item = self.reversal(transfer)
        cancel_reversal(item, actor=self.operator, reason="Replace evidence")
        cancel_reversal(item, actor=self.operator, reason="Replace evidence")
        item = self.reversal(transfer, idempotency_key="replacement")
        submit_reversal(item, actor=self.operator)
        reject_reversal(item, actor=self.approver, reason="Evidence insufficient")
        reject_reversal(item, actor=self.approver, reason="Evidence insufficient")
        with self.assertRaises(ValidationError):
            execute_reversal(item, actor=self.operator)
        self.assertEqual(self.wallet.available_balance, 20000)

    def test_generic_correction_cannot_reverse_treasury_debit(self):
        transfer = self.transfer()
        for kind in ["reversal", "treasury_reversal", "adjustment"]:
            with self.assertRaises(ValidationError):
                create_finance_action_request(action_type=kind, wallet=self.wallet, amount=5000,
                    reason="Bypass", external_reference="BYPASS", idempotency_key="bypass",
                    requested_by=self.operator, related_entry=transfer.ledger_entry)

    def test_reversal_api_lifecycle(self):
        transfer = self.transfer()
        client = APIClient()
        client.force_authenticate(self.operator)
        response = client.post(f"/api/finance/treasury-transfers/{transfer.pk}/reversal-requests/", {
            "reversal_kind": "returned_funds", "reversal_reference": "API-RETURN", "reason": "Refund receipt",
            "idempotency_key": "api-reversal", "evidence": SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 receipt")}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        path = f'/api/finance/action-requests/{response.data["id"]}'
        self.assertEqual(client.post(path + "/submit/").status_code, 200)
        self.assertEqual(client.post(path + "/approve/").status_code, 403)
        client.force_authenticate(self.approver)
        self.assertEqual(client.post(path + "/approve/").status_code, 200)
        self.assertEqual(self.wallet.available_balance, 20000)
        client.force_authenticate(self.operator)
        self.assertEqual(client.post(path + "/execute/").status_code, 200)
        self.assertEqual(client.post(path + "/execute/").status_code, 200)
        self.assertEqual(self.wallet.available_balance, 25000)
