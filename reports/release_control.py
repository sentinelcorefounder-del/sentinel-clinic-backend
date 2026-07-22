"""Shared hospital report-release policy.

Hospital visibility is authorised only by the stored report distribution state
and the matching referral readiness set by Sentinel Ops.
"""

from django.db.models import Q


def is_report_released_to_hospital(report, referral) -> bool:
    return bool(
        report
        and referral
        and referral.report_id == report.id
        and report.report_status == "issued"
        and report.distribution_status == "released_to_hospital"
        and report.hospital_released_at is not None
        and referral.report_ready is True
    )


def hospital_released_referral_q(report_prefix="report"):
    """Return the canonical database predicate for HospitalReferral queries."""
    return Q(
        **{
            f"{report_prefix}__report_status": "issued",
            f"{report_prefix}__distribution_status": "released_to_hospital",
            f"{report_prefix}__hospital_released_at__isnull": False,
            "report_ready": True,
        }
    )


def hospital_visible_report_status(report, referral):
    if not report:
        return "not_created"
    if (
        report.report_status == "issued"
        and not is_report_released_to_hospital(report, referral)
    ):
        return "submitted_to_ops"
    return report.report_status


def hospital_visible_referral_status(referral):
    if (
        referral
        and referral.report_id
        and not is_report_released_to_hospital(referral.report, referral)
        and referral.referral_status in {"report_issued", "completed"}
    ):
        return "submitted_to_ops"
    return referral.referral_status if referral else ""
