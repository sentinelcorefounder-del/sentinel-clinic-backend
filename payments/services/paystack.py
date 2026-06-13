import requests
from django.conf import settings


def initialize_transaction(email: str, amount_kobo: int, reference: str):
    response = requests.post(
        f"{settings.PAYSTACK_BASE_URL}/transaction/initialize",
        json={
            "email": email,
            "amount": amount_kobo,
            "reference": reference,
        },
        headers={
            "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    try:
        data = response.json()
    except Exception:
        data = {"raw_response": response.text}

    if response.status_code >= 400:
        raise Exception(
            f"Paystack returned {response.status_code}: "
            f"{data.get('message', data)}"
        )

    return data


def verify_transaction(reference: str):
    response = requests.get(
        f"{settings.PAYSTACK_BASE_URL}/transaction/verify/{reference}",
        headers={
            "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    try:
        data = response.json()
    except Exception:
        data = {"raw_response": response.text}

    if response.status_code >= 400:
        raise Exception(
            f"Paystack returned {response.status_code}: "
            f"{data.get('message', data)}"
        )

    return data