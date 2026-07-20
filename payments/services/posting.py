from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from finance.models import EncounterFinancialRecord
from finance.services import top_up_wallet
from payments.models import PaymentTransaction


def _money_from_kobo(value):
    return (Decimal(str(value or 0)) / Decimal("100")).quantize(Decimal("0.01"))


@transaction.atomic
def post_verified_payment(payment, verify_payload):
    payment = PaymentTransaction.objects.select_for_update().select_related(
        "wallet", "financial_record"
    ).get(pk=payment.pk)

    if payment.status == PaymentTransaction.Status.POSTED:
        return payment

    data = verify_payload.get("data") or {}
    if verify_payload.get("status") is not True or data.get("status") != "success":
        raise ValidationError("Paystack transaction is not successful.")

    provider_reference = str(data.get("reference") or "")
    if provider_reference != payment.reference:
        raise ValidationError("Paystack reference does not match the internal payment reference.")

    provider_currency = str(data.get("currency") or "NGN").upper()
    if provider_currency != payment.currency.upper():
        raise ValidationError(
            f"Currency mismatch. Expected {payment.currency}, received {provider_currency}."
        )

    received_amount = _money_from_kobo(data.get("amount"))
    if received_amount != payment.expected_amount:
        raise ValidationError(
            f"Amount mismatch. Expected {payment.expected_amount}, received {received_amount}."
        )

    payment.received_amount = received_amount
    payment.provider_payload = verify_payload
    payment.status = PaymentTransaction.Status.VERIFIED
    payment.verified_at = timezone.now()
    payment.failure_reason = ""
    payment.save(update_fields=[
        "received_amount", "provider_payload", "status", "verified_at",
        "failure_reason", "updated_at",
    ])

    if payment.purpose == PaymentTransaction.Purpose.WALLET_TOP_UP:
        if not payment.wallet_id:
            raise ValidationError("Wallet top-up payment has no wallet attached.")
        top_up_wallet(
            wallet=payment.wallet,
            amount=received_amount,
            idempotency_key=f"paystack:{payment.reference}:wallet-credit",
            reference=payment.reference,
            description="Verified Paystack wallet top-up",
            metadata={
                "payment_transaction_id": payment.id,
                "provider": "paystack",
            },
        )

    elif payment.purpose == PaymentTransaction.Purpose.ENCOUNTER_PAYMENT:
        if not payment.financial_record_id:
            raise ValidationError("Encounter payment has no financial record attached.")
        record = EncounterFinancialRecord.objects.select_for_update().get(
            pk=payment.financial_record_id
        )
        if record.currency.upper() != payment.currency.upper():
            raise ValidationError("Payment currency does not match the financial record currency.")
        if record.outstanding_amount != received_amount:
            raise ValidationError(
                f"Financial record outstanding amount is {record.outstanding_amount}, "
                f"but payment received is {received_amount}."
            )
        previous_status = record.status
        now = timezone.now()
        record.outstanding_amount = Decimal("0.00")
        record.status = EncounterFinancialRecord.Status.CAPTURED
        record.financially_releasable = True
        record.secured_at = record.secured_at or now
        record.captured_at = now
        record.exception_reason = ""
        record.save(update_fields=[
            "outstanding_amount", "status", "financially_releasable",
            "secured_at", "captured_at", "exception_reason", "updated_at",
        ])
        from finance.models import FinancialAuditLog
        FinancialAuditLog.objects.create(
            financial_record=record,
            action="paystack_payment_captured",
            previous_status=previous_status,
            new_status=record.status,
            details={
                "payment_transaction_id": payment.id,
                "reference": payment.reference,
                "amount": str(received_amount),
            },
        )
    else:
        raise ValidationError("Unsupported payment purpose.")

    payment.status = PaymentTransaction.Status.POSTED
    payment.posted_at = timezone.now()
    payment.save(update_fields=["status", "posted_at", "updated_at"])
    return payment
