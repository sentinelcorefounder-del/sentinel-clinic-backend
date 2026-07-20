from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    EncounterAllocation,
    EncounterFinancialRecord,
    FinancialAuditLog,
    PartnerContract,
    PricingRule,
)


def _active_for_date(queryset, value):
    return queryset.filter(effective_from__lte=value).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=value)
    )


def resolve_contract(encounter):
    organization = encounter.originating_organization
    if organization is None:
        raise ValidationError("Encounter has no originating organisation.")

    contracts = PartnerContract.objects.filter(
        organization=organization,
        programme=encounter.programme,
        status=PartnerContract.Status.ACTIVE,
    )
    return _active_for_date(contracts, encounter.encounter_date).order_by("-effective_from", "-id").first()


def resolve_pricing_rule(encounter, contract):
    candidates = _active_for_date(
        contract.pricing_rules.filter(
            is_active=True,
            service_type=encounter.encounter_type,
        ),
        encounter.encounter_date,
    )

    # Empty rule dimensions are wildcards. Exact matches are scored higher.
    valid = []
    dimensions = {
        "source_type": encounter.source_type,
        "workflow_route": encounter.workflow_route,
        "payment_responsibility": encounter.payment_responsibility,
    }
    for rule in candidates.prefetch_related("allocation_rules"):
        score = 0
        matches = True
        for field, encounter_value in dimensions.items():
            rule_value = getattr(rule, field)
            if rule_value:
                if rule_value != encounter_value:
                    matches = False
                    break
                score += 1
        if matches:
            valid.append((score, -rule.priority, rule.effective_from, rule.id, rule))

    if not valid:
        return None
    valid.sort(reverse=True)
    return valid[0][-1]


@transaction.atomic
def ensure_financial_record(encounter):
    record, _ = EncounterFinancialRecord.objects.get_or_create(encounter=encounter)
    return record


@transaction.atomic
def price_encounter(encounter, actor=None, force=False):
    record = EncounterFinancialRecord.objects.select_for_update().filter(encounter=encounter).first()
    if record is None:
        record = EncounterFinancialRecord.objects.create(encounter=encounter)

    if record.status not in {
        EncounterFinancialRecord.Status.UNPRICED,
        EncounterFinancialRecord.Status.PRICED,
        EncounterFinancialRecord.Status.AWAITING_PAYMENT,
        EncounterFinancialRecord.Status.EXCEPTION,
    } and not force:
        raise ValidationError("This financial record has progressed beyond the safe repricing stage.")

    contract = resolve_contract(encounter)
    if contract is None:
        record.status = EncounterFinancialRecord.Status.EXCEPTION
        record.exception_reason = "No active partner contract matched this encounter."
        record.save(update_fields=["status", "exception_reason", "updated_at"])
        raise ValidationError(record.exception_reason)

    rule = resolve_pricing_rule(encounter, contract)
    if rule is None:
        record.status = EncounterFinancialRecord.Status.EXCEPTION
        record.contract = contract
        record.exception_reason = "No active pricing rule matched this encounter."
        record.save(update_fields=["status", "contract", "exception_reason", "updated_at"])
        raise ValidationError(record.exception_reason)

    allocations = []
    allocated_total = Decimal("0.00")
    for allocation_rule in rule.allocation_rules.filter(is_active=True).order_by("priority", "id"):
        amount = allocation_rule.calculate(rule.gross_amount)
        allocated_total += amount
        allocations.append((allocation_rule, amount))

    if allocated_total != rule.gross_amount:
        raise ValidationError(
            f"Allocation rules total {allocated_total} {contract.currency}; expected {rule.gross_amount}."
        )

    previous_status = record.status
    record.allocations.all().delete()
    record.contract = contract
    record.pricing_rule = rule
    record.currency = contract.currency
    record.gross_amount = rule.gross_amount
    record.allocated_amount = allocated_total
    record.outstanding_amount = rule.gross_amount
    record.status = EncounterFinancialRecord.Status.AWAITING_PAYMENT
    record.financially_releasable = False
    record.exception_reason = ""
    record.priced_at = timezone.now()
    record.pricing_snapshot = {
        "contract_id": contract.id,
        "contract_name": contract.name,
        "programme": contract.programme,
        "pricing_rule_id": rule.id,
        "pricing_rule_name": rule.name,
        "service_type": rule.service_type,
        "source_type": rule.source_type,
        "workflow_route": rule.workflow_route,
        "payment_responsibility": rule.payment_responsibility,
        "gross_amount": str(rule.gross_amount),
        "currency": contract.currency,
    }
    record.save()

    EncounterAllocation.objects.bulk_create(
        [
            EncounterAllocation(
                financial_record=record,
                allocation_rule=allocation_rule,
                beneficiary_role=allocation_rule.beneficiary_role,
                beneficiary_organization=allocation_rule.beneficiary_organization,
                label=allocation_rule.label,
                amount=amount,
                currency=contract.currency,
                rule_snapshot={
                    "allocation_rule_id": allocation_rule.id,
                    "calculation_type": allocation_rule.calculation_type,
                    "fixed_amount": str(allocation_rule.fixed_amount) if allocation_rule.fixed_amount is not None else None,
                    "percentage": str(allocation_rule.percentage) if allocation_rule.percentage is not None else None,
                },
            )
            for allocation_rule, amount in allocations
        ]
    )

    FinancialAuditLog.objects.create(
        financial_record=record,
        action="encounter_priced",
        previous_status=previous_status,
        new_status=record.status,
        actor=actor,
        details={"gross_amount": str(record.gross_amount), "currency": record.currency},
    )
    return record


def _require_idempotency_key(value):
    value = str(value or "").strip()
    if not value:
        raise ValidationError("An idempotency key is required.")
    return value


def _money(value):
    value = Decimal(str(value)).quantize(Decimal("0.01"))
    if value <= 0:
        raise ValidationError("Amount must be greater than zero.")
    return value


def _audit(record, action, actor=None, previous_status="", details=None):
    FinancialAuditLog.objects.create(
        financial_record=record,
        action=action,
        previous_status=previous_status,
        new_status=record.status,
        actor=actor,
        details=details or {},
    )


@transaction.atomic
def top_up_wallet(wallet, amount, idempotency_key, actor=None, reference="", description="", metadata=None):
    from .models import OrganizationWallet, WalletLedgerEntry

    amount = _money(amount)
    idempotency_key = _require_idempotency_key(idempotency_key)
    wallet = OrganizationWallet.objects.select_for_update().get(pk=wallet.pk)
    existing = WalletLedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    return WalletLedgerEntry.objects.create(
        wallet=wallet,
        entry_type=WalletLedgerEntry.EntryType.TOP_UP,
        available_delta=amount,
        reserved_delta=Decimal("0.00"),
        currency=wallet.currency,
        idempotency_key=idempotency_key,
        reference=reference,
        description=description or "Wallet top up",
        metadata=metadata or {},
        actor=actor,
    )


@transaction.atomic
def adjust_wallet(wallet, available_delta, idempotency_key, actor=None, reference="", description="", metadata=None):
    from .models import OrganizationWallet, WalletLedgerEntry

    available_delta = Decimal(str(available_delta)).quantize(Decimal("0.01"))
    if available_delta == 0:
        raise ValidationError("Adjustment amount cannot be zero.")
    idempotency_key = _require_idempotency_key(idempotency_key)
    wallet = OrganizationWallet.objects.select_for_update().get(pk=wallet.pk)
    existing = WalletLedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    if wallet.available_balance + wallet.credit_limit + available_delta < 0:
        raise ValidationError("Adjustment would exceed the wallet credit limit.")
    return WalletLedgerEntry.objects.create(
        wallet=wallet,
        entry_type=WalletLedgerEntry.EntryType.ADJUSTMENT,
        available_delta=available_delta,
        reserved_delta=Decimal("0.00"),
        currency=wallet.currency,
        idempotency_key=idempotency_key,
        reference=reference,
        description=description or "Wallet adjustment",
        metadata=metadata or {},
        actor=actor,
    )


@transaction.atomic
def reserve_wallet_funds(wallet, financial_record, amount, idempotency_key, actor=None, reference=""):
    from .models import OrganizationWallet, WalletLedgerEntry, WalletReservation

    amount = _money(amount)
    idempotency_key = _require_idempotency_key(idempotency_key)
    wallet = OrganizationWallet.objects.select_for_update().get(pk=wallet.pk)
    financial_record = EncounterFinancialRecord.objects.select_for_update().get(pk=financial_record.pk)

    existing = WalletReservation.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    if not wallet.is_active:
        raise ValidationError("This wallet is inactive.")
    if wallet.currency != financial_record.currency:
        raise ValidationError("Wallet and financial record currencies do not match.")
    if financial_record.status not in {
        EncounterFinancialRecord.Status.AWAITING_PAYMENT,
        EncounterFinancialRecord.Status.PRICED,
    }:
        raise ValidationError("This financial record is not eligible for wallet reservation.")
    if amount > financial_record.outstanding_amount:
        raise ValidationError("Reservation cannot exceed the outstanding amount.")
    if wallet.spendable_balance < amount:
        raise ValidationError("Insufficient wallet balance and credit limit.")

    reservation = WalletReservation.objects.create(
        wallet=wallet,
        financial_record=financial_record,
        amount=amount,
        currency=wallet.currency,
        status=WalletReservation.Status.ACTIVE,
        idempotency_key=idempotency_key,
        reference=reference,
    )
    WalletLedgerEntry.objects.create(
        wallet=wallet,
        entry_type=WalletLedgerEntry.EntryType.SERVICE_RESERVATION,
        available_delta=-amount,
        reserved_delta=amount,
        currency=wallet.currency,
        financial_record=financial_record,
        reservation=reservation,
        idempotency_key=f"{idempotency_key}:ledger",
        reference=reference,
        description=f"Funds reserved for {financial_record}",
        actor=actor,
    )
    previous_status = financial_record.status
    financial_record.status = EncounterFinancialRecord.Status.WALLET_RESERVED
    financial_record.financially_releasable = False
    financial_record.save(update_fields=["status", "financially_releasable", "updated_at"])
    _audit(
        financial_record,
        "wallet_funds_reserved",
        actor=actor,
        previous_status=previous_status,
        details={"reservation_id": reservation.id, "amount": str(amount), "wallet_id": wallet.id},
    )
    return reservation


@transaction.atomic
def capture_wallet_reservation(reservation, amount=None, idempotency_key=None, actor=None, reference=""):
    from .models import WalletLedgerEntry, WalletReservation

    reservation = WalletReservation.objects.select_for_update().select_related(
        "wallet", "financial_record"
    ).get(pk=reservation.pk)
    if idempotency_key:
        existing = WalletLedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return reservation
    amount = _money(amount if amount is not None else reservation.remaining_amount)
    if amount > reservation.remaining_amount:
        raise ValidationError("Capture amount exceeds the remaining reservation.")
    if reservation.status in {WalletReservation.Status.RELEASED, WalletReservation.Status.CAPTURED}:
        raise ValidationError("This reservation is already closed.")

    WalletLedgerEntry.objects.create(
        wallet=reservation.wallet,
        entry_type=WalletLedgerEntry.EntryType.SERVICE_CAPTURE,
        available_delta=Decimal("0.00"),
        reserved_delta=-amount,
        currency=reservation.currency,
        financial_record=reservation.financial_record,
        reservation=reservation,
        idempotency_key=idempotency_key or f"capture:{reservation.id}:{reservation.captured_amount}:{amount}",
        reference=reference or reservation.reference,
        description=f"Captured wallet funds for {reservation.financial_record}",
        actor=actor,
    )
    reservation.captured_amount += amount
    if reservation.remaining_amount == 0:
        reservation.status = WalletReservation.Status.CAPTURED
        reservation.captured_at = timezone.now()
    else:
        reservation.status = WalletReservation.Status.PARTIALLY_CAPTURED
    reservation.save(update_fields=["captured_amount", "status", "captured_at", "updated_at"])

    record = reservation.financial_record
    previous_status = record.status
    record.outstanding_amount = max(Decimal("0.00"), record.outstanding_amount - amount)
    if record.outstanding_amount == 0:
        record.status = EncounterFinancialRecord.Status.CAPTURED
        record.financially_releasable = True
        record.captured_at = timezone.now()
        record.secured_at = record.secured_at or timezone.now()
    else:
        record.status = EncounterFinancialRecord.Status.WALLET_RESERVED
    record.save(update_fields=[
        "outstanding_amount", "status", "financially_releasable", "captured_at", "secured_at", "updated_at"
    ])
    _audit(
        record,
        "wallet_reservation_captured",
        actor=actor,
        previous_status=previous_status,
        details={"reservation_id": reservation.id, "amount": str(amount)},
    )
    return reservation


@transaction.atomic
def release_wallet_reservation(reservation, amount=None, idempotency_key=None, actor=None, reference=""):
    from .models import WalletLedgerEntry, WalletReservation

    reservation = WalletReservation.objects.select_for_update().select_related(
        "wallet", "financial_record"
    ).get(pk=reservation.pk)
    if idempotency_key:
        existing = WalletLedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return reservation
    amount = _money(amount if amount is not None else reservation.remaining_amount)
    if amount > reservation.remaining_amount:
        raise ValidationError("Release amount exceeds the remaining reservation.")
    if reservation.status in {WalletReservation.Status.RELEASED, WalletReservation.Status.CAPTURED}:
        raise ValidationError("This reservation is already closed.")

    WalletLedgerEntry.objects.create(
        wallet=reservation.wallet,
        entry_type=WalletLedgerEntry.EntryType.RESERVATION_RELEASE,
        available_delta=amount,
        reserved_delta=-amount,
        currency=reservation.currency,
        financial_record=reservation.financial_record,
        reservation=reservation,
        idempotency_key=idempotency_key or f"release:{reservation.id}:{reservation.released_amount}:{amount}",
        reference=reference or reservation.reference,
        description=f"Released wallet reservation for {reservation.financial_record}",
        actor=actor,
    )
    reservation.released_amount += amount
    if reservation.remaining_amount == 0:
        reservation.status = WalletReservation.Status.RELEASED
        reservation.released_at = timezone.now()
    else:
        reservation.status = WalletReservation.Status.PARTIALLY_RELEASED
    reservation.save(update_fields=["released_amount", "status", "released_at", "updated_at"])

    record = reservation.financial_record
    previous_status = record.status
    if record.outstanding_amount > 0:
        record.status = EncounterFinancialRecord.Status.AWAITING_PAYMENT
        record.financially_releasable = False
        record.save(update_fields=["status", "financially_releasable", "updated_at"])
    _audit(
        record,
        "wallet_reservation_released",
        actor=actor,
        previous_status=previous_status,
        details={"reservation_id": reservation.id, "amount": str(amount)},
    )
    return reservation


@transaction.atomic
def refund_to_wallet(wallet, amount, idempotency_key, financial_record=None, actor=None, reference="", related_entry=None):
    from .models import OrganizationWallet, WalletLedgerEntry

    amount = _money(amount)
    idempotency_key = _require_idempotency_key(idempotency_key)
    wallet = OrganizationWallet.objects.select_for_update().get(pk=wallet.pk)
    existing = WalletLedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    entry = WalletLedgerEntry.objects.create(
        wallet=wallet,
        entry_type=WalletLedgerEntry.EntryType.REFUND,
        available_delta=amount,
        reserved_delta=Decimal("0.00"),
        currency=wallet.currency,
        financial_record=financial_record,
        related_entry=related_entry,
        idempotency_key=idempotency_key,
        reference=reference,
        description="Wallet refund",
        actor=actor,
    )
    if financial_record:
        previous_status = financial_record.status
        financial_record.status = EncounterFinancialRecord.Status.REFUNDED
        financial_record.financially_releasable = False
        financial_record.save(update_fields=["status", "financially_releasable", "updated_at"])
        _audit(
            financial_record,
            "wallet_refund_recorded",
            actor=actor,
            previous_status=previous_status,
            details={"amount": str(amount), "wallet_id": wallet.id},
        )
    return entry
