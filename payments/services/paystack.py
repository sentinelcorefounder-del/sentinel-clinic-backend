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
    response.raise_for_status()
    return response.json()


def verify_transaction(reference: str):
    response = requests.get(
        f"{settings.PAYSTACK_BASE_URL}/transaction/verify/{reference}",
        headers={
            "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()