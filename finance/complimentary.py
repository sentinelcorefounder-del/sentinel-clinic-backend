"""Non-cash decisions on existing, unpaid service values. Never posts money."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from .models import (
    ComplimentaryRequest, ComplimentaryEvent, EncounterFinancialRecord,
    EncounterAllocation, EncounterSponsorship, FinancialAuditLog,
    ServiceAllowanceReservation, ServicePartnerEarning,
)
from .permissions import has_internal_finance_role


def require_role(actor, role):
    if not has_internal_finance_role(actor, role):
        raise PermissionDenied("An exact internal finance role is required.")


def _eligible(record):
    if (record.disposition != record.Disposition.STANDARD or record.captured_at
            or record.status not in {record.Status.PRICED, record.Status.AWAITING_PAYMENT}
            or record.gross_amount <= 0 or record.outstanding_amount != record.gross_amount
            or not record.pricing_rule_id):
        raise ValidationError("Complimentary disposition requires an existing, fully unpaid standard pricing record.")
    if record.wallet_reservations.exclude(status__in=["released", "cancelled"]).exists():
        raise ValidationError("Release active funding before requesting complimentary disposition.")
    if EncounterSponsorship.objects.filter(financial_record=record).exclude(status__in=["cancelled", "rejected"]).exists():
        raise ValidationError("Cancel active sponsorship before requesting complimentary disposition.")
    if ServiceAllowanceReservation.objects.filter(financial_record=record, status="active").exists():
        raise ValidationError("Active service allowance prevents complimentary disposition.")
    if record.allocations.exclude(status=EncounterAllocation.Status.PENDING_SERVICE).exists():
        raise ValidationError("Earned or settled allocations require a separate correction.")
    if record.allocations.filter(settlement_items__isnull=False).exists() or ServicePartnerEarning.objects.filter(financial_record=record).exists():
        raise ValidationError("Existing payable or settlement prevents complimentary disposition.")
    # External providers cannot lose an entitlement through an internal waiver.
    snapshot = record.encounter.service_delivery_snapshot or {}
    if snapshot.get("provider_type") == "service_partner" or record.encounter.service_session_id:
        raise ValidationError("Externally delivered/session services require a separate payable review.")
    clinic_id = record.encounter.originating_organization_id
    if not clinic_id or clinic_id != record.encounter.patient.assigned_clinic_id:
        raise ValidationError("The performing clinic and encounter organisation must match.")
    for allocation in record.allocations.select_related("beneficiary_organization"):
        org = allocation.beneficiary_organization
        if not org or (org.pk != clinic_id and not org.is_sentinel_treasury):
            raise ValidationError("An external beneficiary cannot be waived by this internal disposition.")


def _event(item, actor, action, source, reason):
    ComplimentaryEvent.objects.create(
        request=item, actor=actor, action=action, source_status=source,
        target_status=item.status, reason=reason,
    )


@transaction.atomic
def request_complimentary(*, financial_record, actor, reason, idempotency_key):
    require_role(actor, "operator")
    reason, key = str(reason or "").strip(), str(idempotency_key or "").strip()
    if not reason or not key or len(key) > 120:
        raise ValidationError("A reason and idempotency key (up to 120 characters) are required.")
    record = EncounterFinancialRecord.objects.select_for_update().get(pk=financial_record.pk)
    existing = ComplimentaryRequest.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.financial_record_id != record.pk or existing.created_by_id != actor.pk or existing.reason != reason:
            raise ValidationError("Idempotency key belongs to a different request.")
        return existing
    if ComplimentaryRequest.objects.filter(financial_record=record, status__in=["submitted", "approved"]).exists():
        raise ValidationError("An active complimentary request already exists.")
    _eligible(record)
    item = ComplimentaryRequest.objects.create(
        financial_record=record, created_by=actor, reason=reason, idempotency_key=key,
        gross_service_value=record.gross_amount, currency=record.currency,
        pricing_snapshot=record.pricing_snapshot,
        allocation_snapshot=list(record.allocations.values("id", "amount", "status", "beneficiary_organization_id")),
    )
    _event(item, actor, "submitted", "", reason)
    return item


@transaction.atomic
def decide_complimentary(item, *, actor, action, reason=""):
    require_role(actor, "operator" if action == "cancel" else "approver")
    # Lock the financial record first, consistently with request creation.
    record = EncounterFinancialRecord.objects.select_for_update().get(pk=item.financial_record_id)
    item = ComplimentaryRequest.objects.select_for_update().get(pk=item.pk)
    if action not in {"approve", "reject", "cancel"}:
        raise ValidationError("Unknown decision.")
    if action != "cancel" and actor.pk == item.created_by_id:
        raise ValidationError("Maker-checker control: the creator cannot decide this request.")
    target = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[action]
    if item.status == target:
        return item
    if item.status != "submitted":
        raise ValidationError("Only submitted requests can be decided; approved dispositions cannot be reversed here.")
    reason = str(reason or "").strip()
    if action != "approve" and not reason:
        raise ValidationError("A decision reason is required.")
    if action == "approve":
        _eligible(record)
        if record.gross_amount != item.gross_service_value or record.currency != item.currency or record.pricing_snapshot != item.pricing_snapshot:
            raise ValidationError("Pricing changed; cancel and submit a new request.")
        allocations = list(record.allocations.select_for_update().values("id", "amount", "status", "beneficiary_organization_id"))
        # JSON stores money as text; compare using the same representation.
        import json
        from django.core.serializers.json import DjangoJSONEncoder
        if json.loads(json.dumps(allocations, cls=DjangoJSONEncoder)) != item.allocation_snapshot:
            raise ValidationError("Allocations changed; cancel and submit a new request.")
        previous = record.status
        record.disposition = record.Disposition.COMPLIMENTARY
        record.payer_type = record.PayerType.WAIVED
        record.payer_organization = None
        record.collecting_organization = None
        record.collector_type = record.CollectorType.NONE
        record.payment_method = record.PaymentMethod.WAIVED
        record.outstanding_amount = Decimal("0.00")
        record.allocated_amount = Decimal("0.00")
        record.financially_releasable = True
        record.status = record.Status.READY_FOR_RELEASE
        record.secured_at = timezone.now()
        record.exception_reason = ""
        record.save()
        record.allocations.update(status=EncounterAllocation.Status.REVERSED, reversed_at=timezone.now())
        FinancialAuditLog.objects.create(
            financial_record=record, actor=actor, action="complimentary_approved",
            previous_status=previous, new_status=record.status,
            details={"request_id": item.pk, "reason": reason or item.reason,
                     "gross_service_value": str(record.gross_amount), "wallet_movement": "0.00",
                     "patient_amount": "0.00", "clinic_amount": "0.00",
                     "prior_allocations": item.allocation_snapshot},
        )
    source = item.status
    item.status, item.decided_by, item.decided_at = target, actor, timezone.now()
    item.decision_reason = reason or item.reason
    item.save(update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"])
    _event(item, actor, target, source, item.decision_reason)
    return item
