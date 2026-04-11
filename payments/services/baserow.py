import requests
from django.conf import settings


def _headers():
    return {
        "Authorization": f"Token {settings.BASEROW_API_TOKEN}",
        "Content-Type": "application/json",
    }


def update_payment_row(row_id: int, data: dict):
    url = f"{settings.BASEROW_BASE_URL}/api/database/rows/table/{settings.BASEROW_PAYMENTS_TABLE_ID}/{row_id}/?user_field_names=true"
    response = requests.patch(url, json=data, headers=_headers(), timeout=30)

    if not response.ok:
        raise Exception(f"Baserow update failed: {response.status_code} {response.text}")

    return response.json()


def list_payment_rows():
    url = f"{settings.BASEROW_BASE_URL}/api/database/rows/table/{settings.BASEROW_PAYMENTS_TABLE_ID}/?user_field_names=true&size=200"
    response = requests.get(url, headers=_headers(), timeout=30)

    if not response.ok:
        raise Exception(f"Baserow list failed: {response.status_code} {response.text}")

    return response.json().get("results", [])


def find_payment_row_by_reference(reference: str):
    rows = list_payment_rows()
    for row in rows:
        if row.get("External Payment Reference") == reference:
            return row
    return None


def update_referral_row(referral_row_id: int, data: dict):
    url = f"{settings.BASEROW_BASE_URL}/api/database/rows/table/{settings.BASEROW_REFERRALS_TABLE_ID}/{referral_row_id}/?user_field_names=true"
    response = requests.patch(url, json=data, headers=_headers(), timeout=30)

    if not response.ok:
        raise Exception(f"Referral update failed: {response.status_code} {response.text}")

    return response.json()