import hashlib
import hmac
import json
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from finance.models import EncounterFinancialRecord, OrganizationWallet
from .models import PaymentTransaction, PaymentWebhookEvent
from .services.paystack import initialize_transaction, verify_transaction
from .services.posting import post_verified_payment


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValidationError("Invalid JSON.") from exc


def _money(value):
    try:
        amount = Decimal(str(value).replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Invalid amount.") from exc
    if amount <= 0:
        raise ValidationError("Amount must be greater than zero.")
    return amount


def _error(exc, status=400):
    messages = exc.messages if hasattr(exc, "messages") else [str(exc)]
    return JsonResponse({"detail": messages}, status=status)


def _valid_paystack_signature(request):
    supplied = request.headers.get("x-paystack-signature", "")
    secret = str(settings.PAYSTACK_SECRET_KEY).encode("utf-8")
    expected = hmac.new(secret, request.body, hashlib.sha512).hexdigest()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


@csrf_exempt
def initialize_paystack_payment(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    try:
        body = _json_body(request)
        email = str(body.get("email") or "").strip()
        purpose = str(body.get("purpose") or "").strip()
        if not email or purpose not in PaymentTransaction.Purpose.values:
            raise ValidationError("email and a valid purpose are required.")

        wallet = None
        financial_record = None
        if purpose == PaymentTransaction.Purpose.WALLET_TOP_UP:
            wallet = OrganizationWallet.objects.select_related("organization").get(
                pk=body.get("wallet_id"), is_active=True
            )
            amount = _money(body.get("amount"))
            minimum = Decimal(str(getattr(settings, "PAYSTACK_MIN_WALLET_TOPUP_NGN", "1000.00")))
            if amount < minimum:
                raise ValidationError(f"Minimum wallet top-up is {minimum} NGN.")
            currency = wallet.currency
        else:
            financial_record = EncounterFinancialRecord.objects.select_related("encounter").get(
                pk=body.get("financial_record_id")
            )
            if financial_record.outstanding_amount <= 0:
                raise ValidationError("This financial record has no outstanding balance.")
            amount = financial_record.outstanding_amount
            currency = financial_record.currency

        if currency.upper() != "NGN":
            raise ValidationError("This Paystack flow currently supports NGN only.")

        requested_reference = str(body.get("reference") or body.get("payment_id") or "").strip()
        reference = requested_reference or f"SENT-{uuid.uuid4().hex.upper()}"
        if PaymentTransaction.objects.filter(reference=reference).exists():
            raise ValidationError("This payment reference already exists.")

        payment = PaymentTransaction.objects.create(
            reference=reference,
            purpose=purpose,
            email=email,
            currency=currency.upper(),
            expected_amount=amount,
            wallet=wallet,
            financial_record=financial_record,
            metadata=body.get("metadata") or {},
        )

        result = initialize_transaction(
            email=email,
            amount_kobo=int(amount * 100),
            reference=reference,
            metadata={
                "sentinel_payment_id": payment.id,
                "purpose": purpose,
                **(body.get("metadata") or {}),
            },
        )
        data = result.get("data") or {}
        authorization_url = data.get("authorization_url")
        if not authorization_url:
            payment.status = PaymentTransaction.Status.FAILED
            payment.failure_reason = "Paystack response did not contain an authorization URL."
            payment.provider_payload = result
            payment.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])
            return JsonResponse({"detail": payment.failure_reason}, status=502)

        payment.status = PaymentTransaction.Status.INITIALIZED
        payment.authorization_url = authorization_url
        payment.provider_access_code = data.get("access_code") or ""
        payment.provider_payload = result
        payment.initialized_at = timezone.now()
        payment.save(update_fields=[
            "status", "authorization_url", "provider_access_code",
            "provider_payload", "initialized_at", "updated_at",
        ])
        return JsonResponse({
            "success": True,
            "payment_id": payment.id,
            "purpose": payment.purpose,
            "authorization_url": payment.authorization_url,
            "reference": payment.reference,
            "amount": str(payment.expected_amount),
            "currency": payment.currency,
        })
    except (ValidationError, OrganizationWallet.DoesNotExist, EncounterFinancialRecord.DoesNotExist) as exc:
        if isinstance(exc, OrganizationWallet.DoesNotExist):
            return JsonResponse({"detail": "Wallet not found."}, status=404)
        if isinstance(exc, EncounterFinancialRecord.DoesNotExist):
            return JsonResponse({"detail": "Financial record not found."}, status=404)
        return _error(exc)
    except Exception as exc:
        return JsonResponse({"detail": "Paystack initialization failed.", "error": str(exc)}, status=502)


@csrf_exempt
def paystack_webhook(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    if not _valid_paystack_signature(request):
        return JsonResponse({"detail": "Invalid Paystack signature."}, status=401)

    try:
        payload = _json_body(request)
    except ValidationError as exc:
        return _error(exc)

    event_name = str(payload.get("event") or "")
    data = payload.get("data") or {}
    reference = str(data.get("reference") or "")
    event_key = hashlib.sha256(request.body).hexdigest()
    event, created = PaymentWebhookEvent.objects.get_or_create(
        event_key=event_key,
        defaults={"event_name": event_name, "reference": reference, "payload": payload},
    )
    if not created and event.processed:
        return JsonResponse({"success": True, "duplicate": True, "reference": reference})
    if event_name != "charge.success" or not reference:
        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at"])
        return JsonResponse({"success": True, "ignored": True})

    try:
        payment = PaymentTransaction.objects.get(reference=reference)
        verification = verify_transaction(reference)
        post_verified_payment(payment, verification)
        event.processed = True
        event.processing_error = ""
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processing_error", "processed_at"])
        return JsonResponse({"success": True, "reference": reference})
    except PaymentTransaction.DoesNotExist:
        event.processing_error = "Matching internal payment transaction not found."
        event.save(update_fields=["processing_error"])
        return JsonResponse({"detail": event.processing_error}, status=404)
    except (ValidationError, Exception) as exc:
        try:
            payment = PaymentTransaction.objects.filter(reference=reference).first()
            if payment:
                payment.mark_exception(exc)
        finally:
            event.processing_error = str(exc)
            event.save(update_fields=["processing_error"])
        return _error(exc, status=400 if isinstance(exc, ValidationError) else 502)


def payment_status(request, reference):
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    try:
        payment = PaymentTransaction.objects.get(reference=reference)
    except PaymentTransaction.DoesNotExist:
        return JsonResponse({"detail": "Payment not found."}, status=404)
    return JsonResponse({
        "reference": payment.reference,
        "purpose": payment.purpose,
        "status": payment.status,
        "expected_amount": str(payment.expected_amount),
        "received_amount": str(payment.received_amount) if payment.received_amount is not None else None,
        "currency": payment.currency,
        "failure_reason": payment.failure_reason,
        "posted_at": payment.posted_at.isoformat() if payment.posted_at else None,
    })
