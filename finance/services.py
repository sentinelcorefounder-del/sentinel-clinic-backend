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
