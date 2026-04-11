import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .services.paystack import initialize_transaction, verify_transaction
from .services.baserow import (
    update_payment_row,
    find_payment_row_by_reference,
    update_referral_row,
)


@csrf_exempt
def initialize_paystack_payment(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    row_id = body.get("row_id")
    email = body.get("email")
    amount = body.get("amount")
    payment_id = body.get("payment_id")

    if not row_id or not email or not amount or not payment_id:
        return JsonResponse(
            {"detail": "row_id, email, amount, and payment_id are required"},
            status=400,
        )

    try:
        amount_kobo = int(float(amount) * 100)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "Invalid amount"}, status=400)

    try:
        result = initialize_transaction(
            email=email,
            amount_kobo=amount_kobo,
            reference=payment_id,
        )
    except Exception as exc:
        return JsonResponse(
            {
                "detail": "Paystack init failed",
                "error": str(exc),
                "row_id": row_id,
                "email": email,
                "amount": amount,
                "payment_id": payment_id,
                "amount_kobo": amount_kobo,
            },
            status=502,
        )

    data = result.get("data", {})
    authorization_url = data.get("authorization_url")
    reference = data.get("reference")

    if not authorization_url or not reference:
        return JsonResponse({"detail": "Missing Paystack response fields"}, status=502)

    try:
        update_payment_row(
            row_id,
            {
                "Payment link": authorization_url,
                "External Payment Reference": reference,
                "Payment Status": "Pending",
                "Payment Action": "Link Created",
            },
        )
    except Exception as exc:
        return JsonResponse({"detail": f"Baserow update failed: {str(exc)}"}, status=502)

    return JsonResponse(
        {
            "success": True,
            "authorization_url": authorization_url,
            "reference": reference,
            "row_id": row_id,
        }
    )


@csrf_exempt
def paystack_webhook(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    event = payload.get("event")
    data = payload.get("data", {})
    reference = data.get("reference")

    if event != "charge.success" or not reference:
        return JsonResponse({"success": True, "ignored": True})

    try:
        verify = verify_transaction(reference)
    except Exception as exc:
        return JsonResponse({"detail": f"Verification failed: {str(exc)}"}, status=502)

    verify_data = verify.get("data", {})

    if verify.get("status") is not True or verify_data.get("status") != "success":
        return JsonResponse({"detail": "Transaction not successful"}, status=400)

    payment_row = find_payment_row_by_reference(reference)
    if not payment_row:
        return JsonResponse({"detail": "Matching payment row not found"}, status=404)

    amount_received = int(int(verify_data.get("amount", 0)) / 100)
    paid_at = verify_data.get("paid_at")

    expected_amount = payment_row.get("Gross amount")
    try:
        expected_amount = float(expected_amount)
    except (TypeError, ValueError):
        expected_amount = None

    if expected_amount is not None and expected_amount != amount_received:
        update_payment_row(
            payment_row["id"],
            {
                "Payment Reference Status": "Exception",
                "Internal Notes": f"Amount mismatch. Expected {expected_amount}, got {amount_received}",
            },
        )
        return JsonResponse({"detail": "Amount mismatch"}, status=400)

    update_payment_row(
        payment_row["id"],
        {
            "Payment Status": "Paid",
            "Amount Received": amount_received,
            "Payment Date/Time": paid_at,
            "Payment Reference Status": "Verified",
            "Payment Action": "Paid",
            "Internal Notes": "Verified via Paystack webhook",
        },
    )

    referral_links = payment_row.get("Referral") or []
    if referral_links:
        referral_row_id = referral_links[0].get("id")
        if referral_row_id:
            update_referral_row(
                referral_row_id,
                {
                    "Referral Status": "ready_for_ops_review",
                    "Ops Notes": "Payment verified via Paystack webhook",
                },
            )

    return JsonResponse({"success": True, "reference": reference})