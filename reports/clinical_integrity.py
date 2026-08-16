import hashlib
import json

from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from uploads.storage import get_private_clinical_storage

from .models import (
    ReportClinicalResponsibility,
    ReportStatusEvent,
    StructuredReport,
    StructuredReportVersion,
)


CLINICAL_FIELDS = (
    "review_date", "left_unaided_va", "left_corrected_va", "left_dr_grade",
    "left_maculopathy_grade", "right_unaided_va", "right_corrected_va",
    "right_dr_grade", "right_maculopathy_grade", "ungradable",
    "urgency_outcome", "recommendation", "next_followup_interval",
    "recall_months", "recall_due_date", "recall_status", "recall_note",
    "generated_clinical_summary", "final_clinical_summary",
    "clinical_summary_overridden", "report_layout", "selected_fundus_upload_ids",
    "selected_ocular_investigation_ids", "attachment_captions", "notes",
)
EDITABLE_STATUSES = {"draft", "under_review", "returned_to_clinic", "ops_rejected"}


class StaleReportError(APIException):
    status_code = 409
    default_detail = "This report changed after it was loaded. Reload and review the latest version."
    default_code = "stale_report"


def _roles(user):
    return set(user.groups.values_list("name", flat=True))


def _strict_branch_access(user, branch):
    if not branch:
        return False
    return user.branch_access.filter(
        branch__organization_id=branch.organization_id,
    ).filter(
        models.Q(branch=branch) | models.Q(has_all_branch_access=True)
    ).exists()


def clinical_authority(user, report):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Exact retinal clinical authority is required.")
    roles = _roles(user)
    authority = "optometrist" if "optometrist" in roles else "reviewer" if "reviewer" in roles else ""
    if not authority:
        raise PermissionDenied("Exact optometrist or qualified retinal-reviewer authority is required.")
    clinic = report.patient.assigned_clinic
    branch = report.encounter.service_branch or report.patient.assigned_branch
    organization = getattr(getattr(user, "organization_link", None), "organization", None)
    if not clinic or not organization or organization.pk != clinic.pk:
        raise PermissionDenied("The clinician is outside the performing clinic.")
    if not _strict_branch_access(user, branch):
        raise PermissionDenied("The clinician does not have access to the encounter branch.")
    return authority, clinic, branch


def ops_report_authority(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Exact internal report-review authority is required.")
    internal = bool(getattr(getattr(user, "security_profile", None), "is_internal_sentinel_staff", False))
    roles = _roles(user)
    authority = "ops_admin" if "ops_admin" in roles else "sentinel_ops" if "sentinel_ops" in roles else ""
    if not internal or not authority:
        raise PermissionDenied("Exact internal Sentinel report-review authority is required.")
    return authority


def _text(value):
    return (value or "").strip()


def accept_responsibility(*, user, report, clinician_name, professional_role, registration_number, reason=""):
    authority, clinic, branch = clinical_authority(user, report)
    clinician_name = _text(clinician_name) or _text(user.get_full_name())
    professional_role = _text(professional_role)
    registration_number = _text(registration_number)
    reason = _text(reason)
    if not clinician_name or not professional_role or not registration_number:
        raise ValidationError("Clinician name, professional role and registration number are required.")
    responsibility = ReportClinicalResponsibility.objects.select_for_update().filter(report=report).first()
    if responsibility and responsibility.current_clinician_id == user.pk:
        return responsibility, authority, False
    if responsibility and not reason:
        raise ValidationError("A professional takeover reason is required.")
    now = timezone.now()
    if responsibility:
        responsibility.previous_clinician = responsibility.current_clinician
        responsibility.current_clinician = user
        responsibility.accepted_by = user
        responsibility.accepted_at = now
        responsibility.authority_used = authority
        responsibility.clinician_name = clinician_name
        responsibility.professional_role = professional_role
        responsibility.registration_number = registration_number
        responsibility.takeover_reason = reason
        responsibility.save()
        responsibility_changed = True
    else:
        responsibility = ReportClinicalResponsibility.objects.create(
            report=report, current_clinician=user, original_clinician=user,
            accepted_by=user, accepted_at=now, authority_used=authority,
            clinician_name=clinician_name, professional_role=professional_role,
            registration_number=registration_number, clinic=clinic, branch=branch,
        )
        responsibility_changed = True
    return responsibility, authority, responsibility_changed


def require_responsible_clinician(user, report):
    authority, clinic, branch = clinical_authority(user, report)
    responsibility = ReportClinicalResponsibility.objects.select_for_update().filter(report=report).first()
    if not responsibility or responsibility.current_clinician_id != user.pk:
        raise PermissionDenied("Explicit clinical responsibility acceptance is required.")
    if responsibility.clinic_id != clinic.pk or responsibility.branch_id != branch.pk:
        raise PermissionDenied("The recorded clinical responsibility is outside the current clinic or branch.")
    return responsibility, authority


def responsibility_snapshot(responsibility):
    if not responsibility:
        return {"historical_author": "unknown"}
    return {
        "user_id": responsibility.current_clinician_id,
        "original_user_id": responsibility.original_clinician_id,
        "name": responsibility.clinician_name,
        "role": responsibility.professional_role,
        "registration_number": responsibility.registration_number,
        "authority_used": responsibility.authority_used,
        "clinic_id": responsibility.clinic_id,
        "clinic_name": responsibility.clinic.name,
        "branch_id": responsibility.branch_id,
        "branch_name": responsibility.branch.name,
        "accepted_at": responsibility.accepted_at.isoformat(),
    }


def clinical_snapshot(report):
    data = {}
    for field in CLINICAL_FIELDS:
        value = getattr(report, field)
        data[field] = value.isoformat() if hasattr(value, "isoformat") else value
    return data


def snapshot_checksum(snapshot):
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def latest_version(report):
    return report.versions.order_by("-version_number").first()


def create_version_if_changed(*, report, editor, responsibility, purpose, correction_note=""):
    snapshot = clinical_snapshot(report)
    checksum = snapshot_checksum(snapshot)
    previous = latest_version(report)
    if previous and previous.checksum_sha256 == checksum:
        return previous, False
    version = StructuredReportVersion.objects.create(
        report=report,
        version_number=(previous.version_number + 1 if previous else 1),
        clinical_snapshot=snapshot,
        checksum_sha256=checksum,
        editor=editor,
        responsibility_snapshot=responsibility_snapshot(responsibility),
        purpose=purpose,
        correction_note=_text(correction_note),
        source_version=previous,
    )
    return version, True


def expected_version(data):
    raw = data.get("expected_version")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError("expected_version is required.")


def assert_expected(report, expected):
    if report.lock_version != expected:
        raise StaleReportError()


def event_once(*, report, event_type, actor, from_status, to_status, source_version=None,
               target_version=None, authority_used="", note="", correction_note="",
               idempotency_key=""):
    if idempotency_key:
        existing = ReportStatusEvent.objects.filter(
            report=report, idempotency_key=idempotency_key
        ).first()
        if existing:
            return existing, False
    event = ReportStatusEvent.objects.create(
        report=report, event_type=event_type, actor=actor,
        from_status=from_status, to_status=to_status,
        source_version=source_version, target_version=target_version,
        authority_used=authority_used, note=note,
        correction_note=correction_note, idempotency_key=idempotency_key,
    )
    return event, True


def bind_issued_pdf(report, version, request=None):
    from .pdf_renderer import ReportPDFRenderer

    if version.pdf_object_key:
        return False
    pdf = ReportPDFRenderer(report=report, request=request, report_format="clinician").build()
    checksum = hashlib.sha256(pdf).hexdigest()
    key = f"clinical-documents/retinal-reports/{report.report_id}/v{version.version_number}.pdf"
    storage = get_private_clinical_storage()
    created = False
    if storage.exists(key):
        with storage.open(key, "rb") as existing:
            if hashlib.sha256(existing.read()).hexdigest() != checksum:
                raise ValidationError("A conflicting issued report document already exists.")
    else:
        saved = storage.save(key, ContentFile(pdf))
        created = True
        if saved != key:
            storage.delete(saved)
            raise ValidationError("The private issued-report key could not be reserved safely.")
    StructuredReportVersion.objects.filter(pk=version.pk).update(
        pdf_object_key=key, pdf_checksum_sha256=checksum,
        pdf_size=len(pdf), pdf_generated_at=timezone.now(),
    )
    version.pdf_object_key = key
    version.pdf_checksum_sha256 = checksum
    version.pdf_size = len(pdf)
    version.pdf_generated_at = timezone.now()
    return created


def delete_bound_pdf(version):
    if version and version.pdf_object_key:
        try:
            get_private_clinical_storage().delete(version.pdf_object_key)
        except Exception:
            pass


# Imported late to keep the role helper compact and avoid a broad branch-service change.
from django.db import models  # noqa: E402
