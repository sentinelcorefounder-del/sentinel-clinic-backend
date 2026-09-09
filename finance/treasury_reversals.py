"""Treasury corrections use FinanceActionRequest; approval never posts cash."""
import hashlib
from pathlib import PurePath

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .complimentary import require_role
from .models import (FinanceActionRequest, FinanceControlAudit, TreasuryTransfer,
                     OrganizationWallet, WalletLedgerEntry, FounderFundedExpense)


def _locked(item):
    transfer_id = FinanceActionRequest.objects.values_list("treasury_transfer_id", flat=True).get(
        pk=item.pk, action_type="treasury_reversal")
    transfer = TreasuryTransfer.objects.select_for_update().get(pk=transfer_id)
    return transfer, FinanceActionRequest.objects.select_for_update().get(pk=item.pk)


def _eligible(transfer):
    if transfer.status != "executed" or not transfer.ledger_entry_id or transfer.reversal_entry_id:
        raise ValidationError("Only an executed, unreversed Treasury transfer can be corrected.")
    entry = transfer.ledger_entry
    if (entry.wallet_id != transfer.wallet_id or entry.currency != transfer.currency
            or entry.available_delta != -transfer.amount or entry.reserved_delta != 0):
        raise ValidationError("Original Treasury debit does not match the transfer.")


def _snapshot(item, transfer):
    if not item.evidence:
        raise ValidationError("Reversal evidence is required.")
    try:
        with item.evidence.open("rb") as evidence:
            digest = hashlib.sha256()
            for chunk in evidence.chunks():
                digest.update(chunk)
    except (OSError, ValueError) as exc:
        raise ValidationError("Reversal evidence is unavailable.") from exc
    return {"transfer_id": transfer.pk, "original_entry_id": transfer.ledger_entry_id,
        "wallet_id": transfer.wallet_id, "amount": str(transfer.amount), "currency": transfer.currency,
        "reversal_kind": item.reversal_kind, "reference": item.external_reference,
        "reason": item.reason, "evidence_name": item.evidence.name, "evidence_sha256": digest.hexdigest(),
        "approved_by_id": item.decided_by_id,
        "approved_at": item.decided_at.isoformat() if item.decided_at else None,
        "requested_by_id": item.requested_by_id, "transfer_creator_id": transfer.created_by_id,
        "transfer_executor_id": transfer.executed_by_id}


def _audit(item, transfer, actor, source):
    from .services import _transfer_event
    metadata = {"request_id": item.pk, "reversal_kind": item.reversal_kind,
                "reference": item.external_reference, "snapshot": item.approved_snapshot,
                "ledger_entry_id": item.posted_entry_id}
    FinanceControlAudit.objects.create(action="treasury_reversal_" + item.status, actor=actor,
        wallet_id=item.wallet_id, action_request=item, before_state={"status": source},
        after_state={"status": item.status}, metadata=metadata)
    _transfer_event(transfer, "reversed" if item.status == "executed" else "reversal_" + item.status,
        actor, transfer.status if item.status != "executed" else "executed", transfer.status,
        f"treasury-reversal:{item.pk}:{item.status}", reason=item.decision_reason or item.reason,
        metadata=metadata)


def _checker(item, transfer, actor):
    if actor.pk in {item.requested_by_id, transfer.created_by_id, transfer.executed_by_id}:
        raise ValidationError("An independent checker must approve or reject the reversal.")


@transaction.atomic
def request_reversal(transfer, *, actor, reversal_kind, reversal_reference, reason, evidence, idempotency_key):
    require_role(actor, "operator")
    transfer = TreasuryTransfer.objects.select_for_update().get(pk=transfer.pk)
    key, reference, reason = (str(value or "").strip() for value in (idempotency_key, reversal_reference, reason))
    if not key or len(key) > 120 or not reference or len(reference) > 120 or not reason:
        raise ValidationError("Reason, independent reference and idempotency key are required (references/keys up to 120 characters).")
    if reversal_kind not in {"returned_funds", "bookkeeping_correction"}:
        raise ValidationError("Choose returned funds or an incorrect debit bookkeeping correction.")
    existing = FinanceActionRequest.objects.filter(idempotency_key=key).first()
    if existing:
        if (existing.treasury_transfer_id != transfer.pk or existing.requested_by_id != actor.pk
                or existing.reversal_kind != reversal_kind or existing.external_reference != reference or existing.reason != reason):
            raise ValidationError("Idempotency key belongs to different request details.")
        return existing
    _eligible(transfer)
    if reference == transfer.external_reference:
        raise ValidationError("A separate reversal reference is required.")
    if not evidence or evidence.size == 0 or evidence.size > 10 * 1024 * 1024:
        raise ValidationError("Evidence is required and must be at most 10 MB.")
    suffix = PurePath(evidence.name).suffix.lower()
    header = evidence.read(8)
    evidence.seek(0)
    signatures = {".pdf": b"%PDF-", ".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}
    if suffix not in signatures or not header.startswith(signatures[suffix]):
        raise ValidationError("Upload PDF, PNG or JPEG evidence with a matching file signature.")
    if FinanceActionRequest.objects.filter(treasury_transfer=transfer,
            status__in=["draft", "pending", "authorized", "executed"]).exists():
        raise ValidationError("This transfer already has an active reversal request.")
    item = FinanceActionRequest(action_type="treasury_reversal", treasury_transfer=transfer,
        wallet_id=transfer.wallet_id, related_entry_id=transfer.ledger_entry_id,
        amount=transfer.amount, currency=transfer.currency, status="draft", requested_by=actor,
        reversal_kind=reversal_kind, external_reference=reference, reason=reason,
        evidence=evidence, idempotency_key=key)
    item.full_clean()
    item.save()
    _audit(item, transfer, actor, "")
    return item


@transaction.atomic
def submit_reversal(item, *, actor):
    require_role(actor, "operator")
    transfer, item = _locked(item)
    if item.status == "pending":
        return item
    if item.status != "draft":
        raise ValidationError("Only draft reversal requests can be submitted.")
    _eligible(transfer)
    _snapshot(item, transfer)  # Fail closed if the uploaded evidence has disappeared.
    item.status = "pending"
    item.save(update_fields=["status", "updated_at"])
    _audit(item, transfer, actor, "draft")
    return item


@transaction.atomic
def approve_reversal(item, *, actor):
    require_role(actor, "approver")
    transfer, item = _locked(item)
    _checker(item, transfer, actor)
    if item.status == "authorized":
        return item
    if item.status != "pending":
        raise ValidationError("Only submitted reversal requests can be approved.")
    _eligible(transfer)
    item.status, item.decided_by, item.decided_at = "authorized", actor, timezone.now()
    item.approved_snapshot = _snapshot(item, transfer)
    item.save(update_fields=["approved_snapshot", "status", "decided_by", "decided_at", "updated_at"])
    _audit(item, transfer, actor, "pending")
    return item


@transaction.atomic
def reject_reversal(item, *, actor, reason):
    return _close(item, actor=actor, reason=reason, reject=True)


@transaction.atomic
def cancel_reversal(item, *, actor, reason):
    return _close(item, actor=actor, reason=reason, reject=False)


def _close(item, *, actor, reason, reject):
    require_role(actor, "approver" if reject else "operator")
    transfer, item = _locked(item)
    if reject:
        _checker(item, transfer, actor)
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("A decision reason is required.")
    target = "rejected" if reject else "cancelled"
    if item.status == target:
        return item
    if item.status not in ({"pending"} if reject else {"draft", "pending"}):
        raise ValidationError("This reversal request cannot be closed in its current state.")
    source = item.status
    item.status, item.decided_by, item.decided_at, item.decision_reason = target, actor, timezone.now(), reason
    item.save(update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"])
    _audit(item, transfer, actor, source)
    return item


@transaction.atomic
def execute_reversal(item, *, actor):
    require_role(actor, "operator")
    transfer, item = _locked(item)
    if item.status == "executed":
        return item
    if item.status != "authorized" or not item.decided_by_id or actor.pk == item.decided_by_id:
        raise ValidationError("An independently approved reversal must be executed by a separate operator.")
    _checker(item, transfer, item.decided_by)
    _eligible(transfer)
    if (item.approved_snapshot != _snapshot(item, transfer) or item.amount != transfer.amount
            or item.currency != transfer.currency or item.wallet_id != transfer.wallet_id
            or item.related_entry_id != transfer.ledger_entry_id):
        raise ValidationError("Approved reversal details or evidence changed; execution is blocked.")
    wallet = OrganizationWallet.objects.select_for_update().get(pk=transfer.wallet_id)
    if TreasuryTransfer.objects.exclude(pk=transfer.pk).filter(reversal_reference=item.external_reference).exists():
        raise ValidationError("This reversal reference has already been used.")
    entry = WalletLedgerEntry.objects.create(wallet=wallet, entry_type="reversal",
        available_delta=item.amount, reserved_delta=0, currency=item.currency,
        related_entry_id=transfer.ledger_entry_id, actor=actor,
        idempotency_key=f"treasury-transfer:{transfer.pk}:reversed", reference=item.external_reference,
        description="Approved Treasury reversal", metadata={"request_id": item.pk,
            "reversal_kind": item.reversal_kind, "reversal_reference": item.external_reference,
            "approved_snapshot": item.approved_snapshot})
    transfer.status, transfer.reversal_entry = "reversed", entry
    transfer.reversal_kind, transfer.reversal_reference = item.reversal_kind, item.external_reference
    transfer.reversal_evidence = item.evidence.name
    transfer.save(update_fields=["status", "reversal_entry", "reversal_kind", "reversal_reference", "reversal_evidence", "updated_at"])
    if transfer.founder_expense_id:
        from .services import _founder_expense_event
        expense = FounderFundedExpense.objects.select_for_update().get(pk=transfer.founder_expense_id)
        if expense.status == "settled":
            expense.status, expense.settled_at = "approved", None
            expense.save(update_fields=["status", "settled_at", "updated_at"])
            _founder_expense_event(expense, "reimbursement_reversed", actor, "settled", "approved",
                f"treasury-reversal:{item.pk}:expense", reason=item.reason, metadata={"request_id": item.pk})
    item.status, item.posted_entry, item.executed_by, item.executed_at = "executed", entry, actor, timezone.now()
    item.save(update_fields=["status", "posted_entry", "executed_by", "executed_at", "updated_at"])
    _audit(item, transfer, actor, "authorized")
    return item
