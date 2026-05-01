import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.urls import reverse


class BasecrowSyncError(Exception):
    pass


def _base_url():
    return getattr(settings, "BASEROW_BASE_URL", "https://api.baserow.io").rstrip("/")


def _api_token():
    return getattr(settings, "BASEROW_API_TOKEN", "")


def _referrals_table_id():
    return getattr(settings, "BASEROW_REFERRALS_TABLE_ID", "")


def _request_json(method, url, payload=None):
    token = _api_token()
    if not token:
        raise BasecrowSyncError("BASEROW_API_TOKEN is not configured.")

    data = None
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
    }

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise BasecrowSyncError(f"Basecrow HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise BasecrowSyncError(f"Basecrow connection error: {exc}") from exc


def _list_rows(search_value):
    table_id = _referrals_table_id()
    if not table_id:
        raise BasecrowSyncError("BASEROW_REFERRALS_TABLE_ID is not configured.")

    params = urlencode({
        "user_field_names": "true",
        "search": search_value or "",
        "size": 100,
    })
    url = f"{_base_url()}/api/database/rows/table/{table_id}/?{params}"
    return _request_json("GET", url)


def _patch_row(row_id, payload):
    table_id = _referrals_table_id()
    params = urlencode({"user_field_names": "true"})
    url = f"{_base_url()}/api/database/rows/table/{table_id}/{row_id}/?{params}"
    return _request_json("PATCH", url, payload)


def find_referral_row(referral_id="", patient_id=""):
    """
    Finds the Basecrow Referrals row.
    Priority:
    1. Exact Referral ID match.
    2. Exact Patient ID match.
    """
    referral_id = (referral_id or "").strip()
    patient_id = (patient_id or "").strip()

    searches = []
    if referral_id:
        searches.append(("Referral ID", referral_id))
    if patient_id:
        searches.append(("Patient ID", patient_id))

    for field_name, value in searches:
        result = _list_rows(value)
        rows = result.get("results", []) if isinstance(result, dict) else []
        for row in rows:
            if str(row.get(field_name, "")).strip() == value:
                return row

    return None


def build_report_pdf_url(request, report):
    path = reverse("report-pdf", kwargs={"pk": report.pk})
    if request is not None:
        return request.build_absolute_uri(path)

    frontend = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    if frontend:
        return f"{frontend}{path}"
    return path


def sync_report_to_basecrow_referral(report, request=None):
    """
    Updates Basecrow/Baserow Referrals table when a report is issued/submitted.

    Required Basecrow env vars:
    - BASEROW_API_TOKEN
    - BASEROW_REFERRALS_TABLE_ID

    Matching uses Referral ID first, then Patient ID.
    """
    patient = report.patient
    referral_id = (getattr(patient, "referral_id", "") or "").strip()
    patient_id = (getattr(patient, "patient_id", "") or "").strip()

    # If local HospitalReferral exists, it is a stronger source for Referral ID.
    local_referral = None
    try:
        from referrals.models import HospitalReferral
        local_referral = (
            HospitalReferral.objects.filter(patient=patient)
            .order_by("-created_at")
            .first()
        )
        if local_referral and local_referral.referral_id:
            referral_id = local_referral.referral_id
    except Exception:
        local_referral = None

    row = find_referral_row(referral_id=referral_id, patient_id=patient_id)
    if not row:
        raise BasecrowSyncError(
            f"No Basecrow referral row found for referral_id='{referral_id}' or patient_id='{patient_id}'."
        )

    report_pdf_url = build_report_pdf_url(request, report)
    submitted_at = report.submitted_to_ops_at.isoformat() if report.submitted_to_ops_at else ""

    payload = {
        "Latest Report ID": report.report_id,
        "Latest Report PK": report.pk,
        "Latest Report Status": report.report_status,
        "Latest Report Submitted At": submitted_at,
        "Report PDF URL": report_pdf_url,
        "Ops Review Status": "pending",
        "Ops Report Queue": True,
    }

    updated = _patch_row(row["id"], payload)

    return {
        "matched_by_referral_id": bool(referral_id),
        "referral_id": referral_id,
        "patient_id": patient_id,
        "baserow_row_id": row["id"],
        "report_pdf_url": report_pdf_url,
        "basecrow_response": updated,
    }


def sync_report_to_local_hospital_referral(report):
    """
    Updates local HospitalReferral so the Hospital Portal can show the report.
    """
    try:
        from referrals.models import HospitalReferral
    except Exception:
        return None

    patient = report.patient
    referral_id = (getattr(patient, "referral_id", "") or "").strip()

    referral = None
    if referral_id:
        referral = HospitalReferral.objects.filter(referral_id=referral_id).first()

    if not referral:
        referral = (
            HospitalReferral.objects.filter(patient=patient)
            .order_by("-created_at")
            .first()
        )

    if not referral:
        return None

    referral.report = report
    referral.report_ready = True
    referral.referral_status = "completed"
    if referral.payout_status == "not_due":
        referral.payout_status = "pending"
    referral.save(
        update_fields=[
            "report",
            "report_ready",
            "referral_status",
            "payout_status",
            "updated_at",
        ]
    )
    return referral
