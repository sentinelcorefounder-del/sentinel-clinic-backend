"""Shared server-authoritative controls for distributable clinical PDFs."""

from finance.models import EncounterFinancialRecord


def audit_clean_pdf_access(*, actor, report_kind, report_id, version_id, audience, context):
    from common.tenant import get_user_organization
    from ops.models import OpsAuditLog
    organization = get_user_organization(actor)
    OpsAuditLog.objects.create(
        actor=actor,
        action="clean_report_accessed",
        entity_type=report_kind,
        entity_id=str(report_id),
        entity_label=f"{report_kind}:{report_id}",
        message="Authorized clean clinical report access.",
        metadata={
            "actor_organization_id": getattr(organization, "id", None),
            "report_version_id": version_id,
            "audience": audience,
            "access_context": context,
        },
    )


def _financial_record(encounter):
    try:
        return encounter.financial_record
    except EncounterFinancialRecord.DoesNotExist:
        return None


def financial_clean_pdf_ready(encounter) -> bool:
    record = _financial_record(encounter)
    return bool(
        encounter.screening_status == "completed"
        and record
        and record.financially_releasable
        and record.captured_at is not None
        and record.status in {
            EncounterFinancialRecord.Status.CAPTURED,
            EncounterFinancialRecord.Status.READY_FOR_RELEASE,
            EncounterFinancialRecord.Status.SETTLED,
        }
    )


def structured_report_is_grandfathered(report) -> bool:
    version = getattr(report, "issued_version", None)
    return bool(
        report.report_status == "issued"
        and version
        and version.legacy_pdf_unbound
    )


def structured_clean_pdf_ready(report, *, hospital_referral=None) -> bool:
    if structured_report_is_grandfathered(report):
        return True
    if report.report_status != "issued" or not report.issued_version_id:
        return False
    if not financial_clean_pdf_ready(report.encounter):
        return False
    if hospital_referral is not None:
        from .release_control import is_report_released_to_hospital
        return is_report_released_to_hospital(report, hospital_referral)
    return True


def targeted_clean_pdf_ready(report, *, hospital_referral=None) -> bool:
    if not financial_clean_pdf_ready(report.encounter):
        return False
    if hospital_referral is not None:
        return bool(
            report.hospital_released_version_id
            and report.hospital_released_at is not None
            and report.encounter.hospital_referral_id == hospital_referral.id
        )
    return bool(report.status == report.Status.FINALIZED and report.finalized_version_id)
