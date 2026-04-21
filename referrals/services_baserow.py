import requests
from django.conf import settings


def _headers():
    return {
        "Authorization": f"Token {settings.BASEROW_API_TOKEN}",
        "Content-Type": "application/json",
    }


def create_hospital_intake_row(data: dict):
    if not settings.BASEROW_HOSPITAL_INTAKE_TABLE_ID:
        raise Exception("BASEROW_HOSPITAL_INTAKE_TABLE_ID is not configured.")

    url = (
        f"{settings.BASEROW_BASE_URL}"
        f"/api/database/rows/table/{settings.BASEROW_HOSPITAL_INTAKE_TABLE_ID}/"
        f"?user_field_names=true"
    )

    response = requests.post(url, json=data, headers=_headers(), timeout=30)

    if not response.ok:
        raise Exception(
            f"Hospital intake create failed: {response.status_code} {response.text}"
        )

    return response.json()


def list_hospital_rows():
    if not settings.BASEROW_HOSPITALS_TABLE_ID:
        raise Exception("BASEROW_HOSPITALS_TABLE_ID is not configured.")

    url = (
        f"{settings.BASEROW_BASE_URL}"
        f"/api/database/rows/table/{settings.BASEROW_HOSPITALS_TABLE_ID}/"
        f"?user_field_names=true&size=200"
    )

    response = requests.get(url, headers=_headers(), timeout=30)

    if not response.ok:
        raise Exception(
            f"Hospitals list failed: {response.status_code} {response.text}"
        )

    return response.json().get("results", [])


def find_hospital_row_id(hospital_id: str = "", name: str = ""):
    rows = list_hospital_rows()

    normalized_hospital_id = (hospital_id or "").strip().lower()
    normalized_name = (name or "").strip().lower()

    for row in rows:
        row_hospital_id = str(row.get("Hospital ID", "")).strip().lower()
        row_name = str(row.get("Hospital Name", "")).strip().lower()

        if normalized_hospital_id and row_hospital_id == normalized_hospital_id:
            return row["id"]

        if normalized_name and row_name == normalized_name:
            return row["id"]

    return None