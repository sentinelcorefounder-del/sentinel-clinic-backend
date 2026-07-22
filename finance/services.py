from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    AllocationRule,
    EncounterAllocation,
    EncounterFinancialRecord,
    FinancialAuditLog,
    PartnerContract,
    PricingRule,
)


def infer_financial_identity(encounter):
    """Return stable finance dimensions without treating pathway as payer."""
    pathway = (
        EncounterFinancialRecord.ServicePathway.CLINIC_DIRECT
        if encounter.source_type == "clinic_direct"
        else EncounterFinancialRecord.ServicePathway.HOSPITAL_REFERRED
    )
    responsibility = (encounter.payment_responsibility or "").strip()
    if responsibility == "patient":
        payer_type = EncounterFinancialRecord.PayerType.PATIENT
        collector_type = EncounterFinancialRecord.CollectorType.SENTINEL
        payment_method = EncounterFinancialRecord.PaymentMethod.PAYSTACK
    elif responsibility in {"hospital", "clinic"}:
        payer_type = EncounterFinancialRecord.PayerType.ORGANIZATION
        collector_type = EncounterFinancialRecord.CollectorType.NONE
        payment_method = EncounterFinancialRecord.PaymentMethod.WALLET
    elif responsibility == "programme":
        payer_type = EncounterFinancialRecord.PayerType.PROGRAMME
        collector_type = EncounterFinancialRecord.CollectorType.PROGRAMME
        payment_method = EncounterFinancialRecord.PaymentMethod.UNSET
    else:
        payer_type = EncounterFinancialRecord.PayerType.WAIVED
        collector_type = EncounterFinancialRecord.CollectorType.NONE
        payment_method = EncounterFinancialRecord.PaymentMethod.WAIVED
    return pathway, payer_type, collector_type, payment_method


def resolve_allocation_beneficiary(encounter, allocation_rule):
    source = allocation_rule.beneficiary_source
    if source == AllocationRule.BeneficiarySource.REFERRING_HOSPITAL:
        referral = getattr(encounter, "hospital_referral", None)
        return getattr(referral, "source_hospital", None)
    if source == AllocationRule.BeneficiarySource.TESTING_CLINIC:
        referral = getattr(encounter, "hospital_referral", None)
        matched_clinic = getattr(referral, "matched_clinic", None)
        if matched_clinic is not None:
            return matched_clinic
        origin = encounter.originating_organization
        if origin is not None and origin.organization_type == "clinic":
            return origin
        return None
    return allocation_rule.beneficiary_organization


def _active_for_date(queryset, value):
    return queryset.filter(effective_from__lte=value).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=value)
    )


def resolve_payer_organization(encounter):
    """Return the organisation that should fund the encounter.

    Hospital referrals are paid from the referring hospital wallet when the
    payment responsibility is hospital. Clinic-direct activity is paid from
    the originating clinic wallet when responsibility is clinic. Patient and
    waived pathways do not use an organisation wallet.
    """
    responsibility = (encounter.payment_responsibility or "").strip()

    if responsibility == "hospital":
        referral = getattr(encounter, "hospital_referral", None)

        if referral and getattr(referral, "source_hospital_id", None):
            return referral.source_hospital

        # Backward-compatible fallback:
        # older and directly-created hospital encounters may not have a
        # linked HospitalReferral record, but their originating organisation
        # is still the hospital responsible for payment.
        return encounter.originating_organization

    if responsibility in {"clinic", "programme"}:
        return encounter.originating_organization

    return None


def resolve_contract(encounter):
    organization = resolve_payer_organization(encounter)
    if organization is None:
        raise ValidationError("Encounter has no organisation responsible for payment.")

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
        beneficiary = resolve_allocation_beneficiary(encounter, allocation_rule)
        if (
            allocation_rule.beneficiary_source != AllocationRule.BeneficiarySource.FIXED
            and beneficiary is None
        ):
            raise ValidationError(
                f"Could not resolve the {allocation_rule.get_beneficiary_source_display().lower()} "
                "for this encounter."
            )
        allocated_total += amount
        allocations.append((allocation_rule, beneficiary, amount))

    if allocated_total != rule.gross_amount:
        raise ValidationError(
            f"Allocation rules total {allocated_total} {contract.currency}; expected {rule.gross_amount}."
        )

    previous_status = record.status
    record.allocations.all().delete()
    record.contract = contract
    record.pricing_rule = rule
    record.payer_organization = resolve_payer_organization(encounter)
    (
        record.service_pathway,
        record.payer_type,
        record.collector_type,
        record.payment_method,
    ) = infer_financial_identity(encounter)
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
        "service_pathway": record.service_pathway,
        "payer_type": record.payer_type,
        "collector_type": record.collector_type,
        "payment_method": record.payment_method,
    }
    record.save()

    EncounterAllocation.objects.bulk_create(
        [
            EncounterAllocation(
                financial_record=record,
                allocation_rule=allocation_rule,
                beneficiary_role=allocation_rule.beneficiary_role,
                beneficiary_organization=beneficiary,
                beneficiary_source=allocation_rule.beneficiary_source,
                label=allocation_rule.label,
                amount=amount,
                currency=contract.currency,
                rule_snapshot={
                    "allocation_rule_id": allocation_rule.id,
                    "beneficiary_source": allocation_rule.beneficiary_source,
                    "beneficiary_organization_id": beneficiary.id if beneficiary else None,
                    "calculation_type": allocation_rule.calculation_type,
                    "fixed_amount": str(allocation_rule.fixed_amount) if allocation_rule.fixed_amount is not None else None,
                    "percentage": str(allocation_rule.percentage) if allocation_rule.percentage is not None else None,
                },
            )
            for allocation_rule, beneficiary, amount in allocations
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
def submit_bank_transfer_proof(funding_request, proof, actor=None):
    from .models import BankTransferFundingRequest

    funding_request = BankTransferFundingRequest.objects.select_for_update().get(pk=funding_request.pk)
    if funding_request.status not in {
        BankTransferFundingRequest.Status.AWAITING_TRANSFER,
        BankTransferFundingRequest.Status.PROOF_SUBMITTED,
    }:
        raise ValidationError("Proof cannot be submitted in the current status.")
    if funding_request.expires_at and funding_request.expires_at <= timezone.now():
        funding_request.status = BankTransferFundingRequest.Status.EXPIRED
        funding_request.save(update_fields=["status", "updated_at"])
        raise ValidationError("This funding request has expired.")
    if not proof:
        raise ValidationError("Transfer proof is required.")
    funding_request.proof = proof
    funding_request.proof_submitted_at = timezone.now()
    funding_request.status = BankTransferFundingRequest.Status.PROOF_SUBMITTED
    funding_request.save(update_fields=["proof", "proof_submitted_at", "status", "updated_at"])
    return funding_request


@transaction.atomic
def verify_bank_transfer(funding_request, received_amount, bank_transaction_reference, value_date, actor=None, notes=""):
    from .models import BankTransferFundingRequest

    funding_request = BankTransferFundingRequest.objects.select_for_update().get(pk=funding_request.pk)
    if funding_request.status not in {
        BankTransferFundingRequest.Status.PROOF_SUBMITTED,
        BankTransferFundingRequest.Status.UNDER_VERIFICATION,
    }:
        raise ValidationError("This funding request is not awaiting verification.")
    received_amount = _money(received_amount)
    bank_transaction_reference = str(bank_transaction_reference or "").strip()
    if not bank_transaction_reference:
        raise ValidationError("The bank transaction reference is required.")
    if BankTransferFundingRequest.objects.exclude(pk=funding_request.pk).filter(
        bank_transaction_reference=bank_transaction_reference
    ).exists():
        raise ValidationError("This bank transaction reference has already been used.")
    if not value_date:
        raise ValidationError("The bank value date is required.")

    funding_request.received_amount = received_amount
    funding_request.bank_transaction_reference = bank_transaction_reference
    funding_request.value_date = value_date
    funding_request.verified_by = actor
    funding_request.verified_at = timezone.now()
    funding_request.notes = notes or funding_request.notes
    if received_amount < funding_request.requested_amount:
        funding_request.status = BankTransferFundingRequest.Status.UNDERPAID
    elif received_amount > funding_request.requested_amount:
        funding_request.status = BankTransferFundingRequest.Status.OVERPAID
    else:
        funding_request.status = BankTransferFundingRequest.Status.VERIFIED
    funding_request.save()
    return funding_request


@transaction.atomic
def approve_bank_transfer(funding_request, actor=None):
    from .models import BankTransferFundingRequest, WalletLedgerEntry

    funding_request = BankTransferFundingRequest.objects.select_for_update().select_related("wallet").get(
        pk=funding_request.pk
    )
    if funding_request.status == BankTransferFundingRequest.Status.CREDITED:
        return funding_request
    if funding_request.status not in {
        BankTransferFundingRequest.Status.VERIFIED,
        BankTransferFundingRequest.Status.UNDERPAID,
        BankTransferFundingRequest.Status.OVERPAID,
    }:
        raise ValidationError("Only a verified transfer can be approved.")
    if funding_request.received_amount is None:
        raise ValidationError("The received amount has not been verified.")

    entry = top_up_wallet(
        wallet=funding_request.wallet,
        amount=funding_request.received_amount,
        idempotency_key=f"bank-transfer:{funding_request.pk}:credit",
        actor=actor,
        reference=funding_request.bank_transaction_reference,
        description=f"Approved bank transfer {funding_request.request_reference}",
        metadata={"bank_transfer_funding_request_id": funding_request.pk},
    )
    funding_request.status = BankTransferFundingRequest.Status.CREDITED
    funding_request.approved_by = actor
    funding_request.approved_at = timezone.now()
    funding_request.ledger_entry = entry
    funding_request.save(update_fields=["status", "approved_by", "approved_at", "ledger_entry", "updated_at"])
    return funding_request


@transaction.atomic
def reject_bank_transfer(funding_request, reason, actor=None):
    from .models import BankTransferFundingRequest

    funding_request = BankTransferFundingRequest.objects.select_for_update().get(pk=funding_request.pk)
    if funding_request.status in {
        BankTransferFundingRequest.Status.CREDITED,
        BankTransferFundingRequest.Status.REVERSED,
    }:
        raise ValidationError("A credited or reversed funding request cannot be rejected.")
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("A rejection reason is required.")
    funding_request.status = BankTransferFundingRequest.Status.REJECTED
    funding_request.rejection_reason = reason
    funding_request.verified_by = actor
    funding_request.verified_at = timezone.now()
    funding_request.save(update_fields=["status", "rejection_reason", "verified_by", "verified_at", "updated_at"])
    return funding_request


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


@transaction.atomic
def reserve_financial_record_from_originating_wallet(financial_record, actor=None, reference=""):
    from .models import OrganizationWallet, WalletReservation

    record = EncounterFinancialRecord.objects.select_for_update().select_related(
        "encounter", "encounter__originating_organization",
        "payer_organization"
    ).get(pk=financial_record.pk)
    if record.status not in {
        EncounterFinancialRecord.Status.AWAITING_PAYMENT,
        EncounterFinancialRecord.Status.PRICED,
    }:
        existing = record.wallet_reservations.filter(
            status__in=[
                WalletReservation.Status.ACTIVE,
                WalletReservation.Status.PARTIALLY_CAPTURED,
            ]
        ).first()
        if existing:
            return existing
        raise ValidationError("This financial record is not eligible for an automatic wallet reservation.")
    organization = record.payer_organization or resolve_payer_organization(record.encounter)
    if organization is None:
        raise ValidationError("No organisation wallet applies to this payment pathway.")
    try:
        wallet = OrganizationWallet.objects.get(
            organization=organization, currency=record.currency, is_active=True
        )
    except OrganizationWallet.DoesNotExist as exc:
        raise ValidationError("The originating organisation has no active wallet for this currency.") from exc
    return reserve_wallet_funds(
        wallet=wallet,
        financial_record=record,
        amount=record.outstanding_amount,
        idempotency_key=f"financial-record:{record.id}:auto-reserve",
        actor=actor,
        reference=reference or f"EFR-{record.id}",
    )


@transaction.atomic
def capture_financial_record_wallet_reservation(financial_record, actor=None, reference=""):
    from .models import WalletReservation

    record = EncounterFinancialRecord.objects.select_for_update().get(pk=financial_record.pk)
    reservation = record.wallet_reservations.filter(
        status__in=[WalletReservation.Status.ACTIVE, WalletReservation.Status.PARTIALLY_CAPTURED]
    ).order_by("created_at").first()
    if not reservation:
        raise ValidationError("No active wallet reservation exists for this financial record.")
    return capture_wallet_reservation(
        reservation,
        amount=reservation.remaining_amount,
        idempotency_key=f"financial-record:{record.id}:auto-capture",
        actor=actor,
        reference=reference or reservation.reference,
    )


@transaction.atomic
def earn_financial_record_allocations(financial_record, actor=None):
    """Mark frozen shares as earned exactly once; callers decide the clinical trigger."""
    record = EncounterFinancialRecord.objects.select_for_update().get(pk=financial_record.pk)
    if not record.financially_releasable:
        raise ValidationError("This financial record is not covered and cannot earn allocations.")
    pending = record.allocations.select_for_update().filter(
        status=EncounterAllocation.Status.PENDING_SERVICE
    )
    if not pending.exists():
        return record
    earned_at = timezone.now()
    count = pending.update(status=EncounterAllocation.Status.EARNED, earned_at=earned_at)
    _audit(
        record,
        "allocations_earned",
        actor=actor,
        previous_status=record.status,
        details={"allocation_count": count, "earned_at": earned_at.isoformat()},
    )
    return record


@transaction.atomic
def capture_finance_for_hospital_publication(encounter, actor=None):
    """Cover and earn a hospital-referred service at the publication boundary.

    Report submission/clinical completion must never capture a hospital-funded
    service. The controlled Ops release endpoint is the only automatic capture
    boundary for this pathway.
    """
    record = EncounterFinancialRecord.objects.select_for_update().filter(
        encounter=encounter
    ).first()
    if record is None:
        record = price_encounter(encounter, actor=actor)
    else:
        record.refresh_from_db()

    if record.service_pathway != EncounterFinancialRecord.ServicePathway.HOSPITAL_REFERRED:
        raise ValidationError("This financial trigger only applies to hospital-referred services.")

    if record.status == EncounterFinancialRecord.Status.WALLET_RESERVED:
        capture_financial_record_wallet_reservation(
            record,
            actor=actor,
            reference=f"EFR-{record.id}-HOSPITAL-PUBLICATION",
        )
        record.refresh_from_db()

    if not record.financially_releasable:
        raise ValidationError(
            "PAYMENT_REQUIRED: This report is on financial hold until its service is fully funded."
        )

    earn_financial_record_allocations(record, actor=actor)
    record.refresh_from_db()
    return record


@transaction.atomic
def create_settlement_batch(beneficiary_organization, period_start, period_end, currency="NGN", actor=None):
    from .models import EncounterAllocation, SettlementBatch, SettlementItem

    if not period_start or not period_end:
        raise ValidationError("Valid settlement period_start and period_end dates are required.")
    if period_end < period_start:
        raise ValidationError("Settlement period end cannot precede its start.")
    allocations = EncounterAllocation.objects.select_for_update().filter(
        beneficiary_organization=beneficiary_organization,
        currency=currency,
        status=EncounterAllocation.Status.EARNED,
        financial_record__financially_releasable=True,
        financial_record__captured_at__date__gte=period_start,
        financial_record__captured_at__date__lte=period_end,
        settlement_item__isnull=True,
    )
    if not allocations.exists():
        raise ValidationError("No unsettled allocations were found for this beneficiary and period.")
    batch = SettlementBatch.objects.create(
        beneficiary_organization=beneficiary_organization,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
    )
    items = [
        SettlementItem(batch=batch, allocation=a, amount=a.amount, currency=a.currency)
        for a in allocations
    ]
    SettlementItem.objects.bulk_create(items)
    batch.total_amount = sum((item.amount for item in items), Decimal("0.00"))
    batch.save(update_fields=["total_amount", "updated_at"])
    return batch


@transaction.atomic
def approve_settlement_batch(batch, actor=None):
    from .models import EncounterAllocation, SettlementBatch

    batch = SettlementBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status != SettlementBatch.Status.DRAFT:
        raise ValidationError("Only draft settlement batches can be approved.")
    batch.status = SettlementBatch.Status.APPROVED
    batch.approved_by = actor
    batch.approved_at = timezone.now()
    batch.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    EncounterAllocation.objects.filter(
        settlement_item__batch=batch,
        status=EncounterAllocation.Status.EARNED,
    ).update(status=EncounterAllocation.Status.SETTLEMENT_PENDING)
    return batch


@transaction.atomic
def mark_settlement_batch_paid(batch, external_reference, actor=None):
    from .models import EncounterAllocation, EncounterFinancialRecord, SettlementBatch

    batch = SettlementBatch.objects.select_for_update().prefetch_related(
        "items__allocation__financial_record"
    ).get(pk=batch.pk)
    if batch.status != SettlementBatch.Status.APPROVED:
        raise ValidationError("Only approved settlement batches can be marked paid.")
    external_reference = str(external_reference or "").strip()
    if not external_reference:
        raise ValidationError("An external settlement reference is required.")
    batch.status = SettlementBatch.Status.PAID
    batch.external_reference = external_reference
    batch.paid_at = timezone.now()
    batch.save(update_fields=["status", "external_reference", "paid_at", "updated_at"])
    EncounterAllocation.objects.filter(settlement_item__batch=batch).update(
        status=EncounterAllocation.Status.SETTLED,
        settled_at=batch.paid_at,
    )

    record_ids = {item.allocation.financial_record_id for item in batch.items.all()}
    for record in EncounterFinancialRecord.objects.select_for_update().filter(id__in=record_ids):
        if not record.allocations.exclude(settlement_item__batch__status=SettlementBatch.Status.PAID).exists():
            previous_status = record.status
            record.status = EncounterFinancialRecord.Status.SETTLED
            record.settled_at = timezone.now()
            record.save(update_fields=["status", "settled_at", "updated_at"])
            _audit(
                record,
                "settlement_paid",
                actor=actor,
                previous_status=previous_status,
                details={"settlement_batch_id": batch.id, "external_reference": external_reference},
            )
    return batch


@transaction.atomic
def approve_financial_record_credit(financial_record, actor=None):
    record = EncounterFinancialRecord.objects.select_for_update().select_related("contract").get(
        pk=financial_record.pk
    )
    if not record.contract or not record.contract.credit_allowed:
        raise ValidationError("The active contract does not permit credit.")
    previous_status = record.status
    record.status = EncounterFinancialRecord.Status.APPROVED_CREDIT
    record.secured_at = record.secured_at or timezone.now()
    record.financially_releasable = False
    record.exception_reason = ""
    record.save(update_fields=[
        "status", "secured_at", "financially_releasable", "exception_reason", "updated_at"
    ])
    _audit(record, "credit_approved", actor=actor, previous_status=previous_status)
    return record


@transaction.atomic
def cancel_financial_record(financial_record, actor=None, reason="Encounter cancelled"):
    from .models import WalletReservation

    record = EncounterFinancialRecord.objects.select_for_update().get(pk=financial_record.pk)
    for reservation in record.wallet_reservations.filter(
        status__in=[WalletReservation.Status.ACTIVE, WalletReservation.Status.PARTIALLY_CAPTURED]
    ):
        if reservation.remaining_amount > 0:
            release_wallet_reservation(
                reservation,
                amount=reservation.remaining_amount,
                idempotency_key=f"financial-record:{record.id}:cancel-release:{reservation.id}",
                actor=actor,
                reference=f"EFR-{record.id}-CANCEL",
            )
    previous_status = record.status
    record.refresh_from_db()
    if record.status != EncounterFinancialRecord.Status.CAPTURED:
        record.status = EncounterFinancialRecord.Status.CANCELLED
        record.financially_releasable = False
        record.exception_reason = reason
        record.save(update_fields=["status", "financially_releasable", "exception_reason", "updated_at"] )
        _audit(record, "encounter_finance_cancelled", actor=actor, previous_status=previous_status, details={"reason": reason})
    return record


@transaction.atomic
def sync_encounter_finance_lifecycle(encounter, actor=None):
    """Idempotently align finance with the encounter clinical lifecycle.

    scheduled/in-progress: price and secure by wallet or approved credit
    clinic-direct completed: capture and earn the service
    hospital-referred completed: retain the reservation until Ops publication
    cancelled: release unused reservation and cancel finance record

    Clinical work is not deleted when finance cannot progress. Instead the
    financial record is placed in exception for Ops follow-up.
    """
    record = ensure_financial_record(encounter)

    if encounter.screening_status == "cancelled":
        return cancel_financial_record(record, actor=actor)

    if record.status in {
        EncounterFinancialRecord.Status.UNPRICED,
        EncounterFinancialRecord.Status.EXCEPTION,
    }:
        record = price_encounter(encounter, actor=actor, force=True)

    responsibility = (encounter.payment_responsibility or "").strip()
    if responsibility == "waived":
        previous_status = record.status
        record.status = EncounterFinancialRecord.Status.FINANCIALLY_SECURED
        record.outstanding_amount = Decimal("0.00")
        record.secured_at = record.secured_at or timezone.now()
        record.exception_reason = ""
        record.save(update_fields=["status", "outstanding_amount", "secured_at", "exception_reason", "updated_at"] )
        _audit(record, "payment_waived", actor=actor, previous_status=previous_status)
        return record

    if responsibility in {"hospital", "clinic", "programme"} and record.status in {
        EncounterFinancialRecord.Status.AWAITING_PAYMENT,
        EncounterFinancialRecord.Status.PRICED,
    }:
        try:
            reserve_financial_record_from_originating_wallet(
                record, actor=actor, reference=f"EFR-{record.id}-AUTO"
            )
        except ValidationError:
            if record.contract and record.contract.credit_allowed:
                approve_financial_record_credit(record, actor=actor)
            else:
                raise
        record.refresh_from_db()

    if (
        encounter.screening_status == "completed"
        and record.service_pathway == EncounterFinancialRecord.ServicePathway.CLINIC_DIRECT
    ):
        if record.status == EncounterFinancialRecord.Status.WALLET_RESERVED:
            capture_financial_record_wallet_reservation(
                record, actor=actor, reference=f"EFR-{record.id}-COMPLETE"
            )
            record.refresh_from_db()
        record.refresh_from_db()
        if record.financially_releasable:
            earn_financial_record_allocations(record, actor=actor)

    return record
