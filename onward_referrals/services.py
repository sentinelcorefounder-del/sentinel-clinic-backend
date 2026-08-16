import hashlib

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.services import record_patient_event
from encounters.models import OcularDiagnosticAssessment, ScreeningEncounter
from organizations.models import Organization
from organizations.notification_service import notify_organization
from organizations.report_branding import resolve_report_branding
from reports.models import StructuredReport
from uploads.storage import get_private_clinical_storage
from users.clinical_authority import normalized_professional_credentials

from .documents import render_onward_referral
from .models import (
    EncounterResponsibleOptometrist,
    OnwardReferral,
    OnwardReferralAvailability,
    OnwardReferralEvent,
    OnwardReferralVersion,
)
from .permissions import can_distribute, require_clinical_author, require_current_author


OCULAR_OUTCOMES = {"refer_routine", "refer_urgent", "refer_emergency"}
RETINAL_OUTCOMES = {"urgent_referral", "ophthalmology_required"}
URGENCY_RANK = {"routine": 1, "expedited": 2, "urgent": 3, "emergency": 4}


def validate_draft(version):
    if version.urgency not in URGENCY_RANK:
        raise ValidationError("Choose a supported onward-referral urgency.")
    if not version.referral_reason.strip() or not version.requested_specialist_action.strip():
        raise ValidationError("Referral reason and requested specialist action are required.")
    if len(version.referral_reason) > 255:
        raise ValidationError("Referral reason must be 255 characters or fewer.")
    if len(version.emergency_escalation_note) > 255:
        raise ValidationError("Emergency escalation note must be 255 characters or fewer.")


def clinical_records(encounter):
    ocular = OcularDiagnosticAssessment.objects.filter(encounter=encounter).first()
    retinal = StructuredReport.objects.filter(encounter=encounter).first()
    return ocular, retinal


def eligible_sources(encounter):
    ocular, retinal = clinical_records(encounter)
    sources = []
    if ocular and ocular.completed_at and ocular.management_outcome in OCULAR_OUTCOMES:
        sources.append("ocular")
    if retinal and retinal.urgency_outcome in RETINAL_OUTCOMES:
        sources.append("retinal")
    return sources, ocular, retinal


def eligibility(encounter):
    sources, ocular, retinal = eligible_sources(encounter)
    responsibility = EncounterResponsibleOptometrist.objects.filter(encounter=encounter).first()
    return {
        "encounter_completed": encounter.screening_status == "completed",
        "eligible_sources": sources,
        "responsible_optometrist": responsibility,
        "ocular_assessment": ocular,
        "retinal_report": retinal,
        "eligible": bool(
            encounter.screening_status == "completed" and sources and responsibility
        ),
    }


@transaction.atomic
def accept_responsibility(*, user, encounter, clinician_name, professional_role, registration_number, reason=""):
    clinic = encounter.patient.assigned_clinic
    branch = encounter.service_branch or encounter.patient.assigned_branch
    if not clinic or not branch:
        raise ValidationError("The encounter requires a performing clinic and branch.")
    authority = require_clinical_author(user, clinic, branch)
    credentials = normalized_professional_credentials(
        clinician_name=clinician_name,
        professional_role=professional_role,
        registration_number=registration_number,
        fallback_name=user.get_full_name(),
    )
    reason = (reason or "").strip()
    if not credentials:
        raise ValidationError("Name, professional role and registration number are required.")
    clinician_name, professional_role, registration_number = credentials
    responsibility = EncounterResponsibleOptometrist.objects.select_for_update().filter(encounter=encounter).first()
    previous = None
    if responsibility:
        if responsibility.optometrist_id == user.id:
            return responsibility
        if not reason:
            raise ValidationError("A takeover reason is required.")
        previous = responsibility.optometrist
        responsibility.previous_optometrist = previous
        responsibility.optometrist = user
        responsibility.clinician_name = clinician_name
        responsibility.professional_role = professional_role
        responsibility.registration_number = registration_number
        responsibility.accepted_by = user
        responsibility.accepted_at = timezone.now()
        responsibility.takeover_reason = reason
        responsibility.save()
        event_type = "onward_responsibility_taken_over"
    else:
        ocular = OcularDiagnosticAssessment.objects.filter(encounter=encounter).first()
        if ocular and ocular.completed_by_id and ocular.completed_by_id != user.id and not reason:
            raise ValidationError("A responsibility reason is required when the completing clinician differs.")
        responsibility = EncounterResponsibleOptometrist.objects.create(
            encounter=encounter, optometrist=user, original_optometrist=user,
            clinician_name=clinician_name, professional_role=professional_role,
            registration_number=registration_number, accepted_by=user,
            accepted_at=timezone.now(), takeover_reason=reason,
        )
        event_type = "onward_responsibility_accepted"
    record_patient_event(
        patient=encounter.patient,
        event_key=f"encounter:{encounter.pk}:{event_type}:{responsibility.accepted_at.isoformat()}",
        category="referral", event_type=event_type,
        title="Onward-referral clinical responsibility recorded",
        description="An authorized clinical professional explicitly accepted responsibility.",
        source_type="encounter", source_id=encounter.pk,
        encounter_id=encounter.encounter_id, actor=user, organization=clinic,
        visibility="clinic_ops",
        metadata={
            "previous_optometrist_id": getattr(previous, "id", None),
            "reason_recorded": bool(reason), "authority_used": authority,
        },
    )
    return responsibility


def _validate_sources(encounter, requested):
    available, ocular, retinal = eligible_sources(encounter)
    requested = list(dict.fromkeys(requested or []))
    if encounter.programme == "combined_assessment" and not requested:
        raise ValidationError("Select the clinical source or sources for this combined assessment.")
    if not requested:
        requested = available
    if not requested or any(source not in {"ocular", "retinal"} for source in requested):
        raise ValidationError("Select an eligible professional clinical source.")
    if any(source not in available for source in requested):
        raise ValidationError("A selected clinical source does not support onward referral.")
    return requested, ocular, retinal


def _recipient(*, encounter, route, recipient_organization_id):
    if route == OnwardReferral.Route.ORIGINATING_HOSPITAL:
        referral = encounter.hospital_referral
        if not referral or not referral.source_hospital_id:
            raise ValidationError("This encounter has no originating hospital.")
        return referral.source_hospital
    if route == OnwardReferral.Route.REGISTERED_HOSPITAL:
        hospital = Organization.objects.filter(
            pk=recipient_organization_id, organization_type="hospital", is_active=True
        ).first()
        if not hospital:
            raise ValidationError("Select an active registered hospital.")
        return hospital
    if route == OnwardReferral.Route.CLINIC_DOWNLOAD:
        return None
    raise ValidationError("Choose a supported onward-referral route.")


@transaction.atomic
def create_referral(*, user, encounter, clinical_sources, route, recipient_organization_id=None, recipient_department="", data=None):
    data = data or {}
    state = eligibility(encounter)
    if not state["encounter_completed"]:
        raise ValidationError("The encounter must be completed first.")
    if not state["responsible_optometrist"]:
        raise ValidationError("A responsible clinical professional must explicitly accept responsibility first.")
    branch = encounter.service_branch or encounter.patient.assigned_branch
    require_clinical_author(user, encounter.patient.assigned_clinic, branch)
    if state["responsible_optometrist"].optometrist_id != user.id:
        raise PermissionDenied("Only the responsible clinical professional may create the clinical draft.")
    sources, ocular, retinal = _validate_sources(encounter, clinical_sources)
    recipient = _recipient(
        encounter=encounter, route=route,
        recipient_organization_id=recipient_organization_id,
    )
    referral = OnwardReferral.objects.create(
        patient=encounter.patient, encounter=encounter,
        originating_clinic=encounter.patient.assigned_clinic,
        branch=branch,
        original_hospital_referral=encounter.hospital_referral,
        retinal_report=retinal if "retinal" in sources else None,
        ocular_assessment=ocular if "ocular" in sources else None,
        clinical_sources=sources, route=route, created_by=user,
    )
    version = OnwardReferralVersion.objects.create(
        referral=referral, version_number=1,
        urgency=data.get("urgency", "routine"),
        referral_reason=(data.get("referral_reason") or "").strip(),
        requested_specialist_action=(data.get("requested_specialist_action") or "").strip(),
        relevant_history=(data.get("relevant_history") or "").strip(),
        pertinent_findings=(data.get("pertinent_findings") or "").strip(),
        professional_impression=(data.get("professional_impression") or "").strip(),
        management_provided=(data.get("management_provided") or "").strip(),
        include_patient_phone=bool(data.get("include_patient_phone")),
        recipient_organization=recipient,
        recipient_department=(recipient_department or "").strip(),
        responsible_optometrist=user, created_by=user,
    )
    validate_draft(version)
    referral.current_version = version
    referral.save(update_fields=["current_version", "updated_at"])
    OnwardReferralEvent.objects.create(
        referral=referral, version=version, event_type="draft_created", actor=user,
        safe_note="Onward-referral draft created.", metadata={"clinical_sources": sources},
    )
    return referral


def _minimum_urgency(referral):
    minimum = "routine"
    ocular = referral.ocular_assessment
    retinal = referral.retinal_report
    if ocular:
        mapped = {"refer_emergency": "emergency", "refer_urgent": "urgent", "refer_routine": "routine"}.get(ocular.management_outcome, "routine")
        if URGENCY_RANK[mapped] > URGENCY_RANK[minimum]:
            minimum = mapped
    if retinal and retinal.urgency_outcome == "urgent_referral" and URGENCY_RANK["urgent"] > URGENCY_RANK[minimum]:
        minimum = "urgent"
    return minimum


def _snapshots(referral, version, responsibility):
    patient = referral.patient
    master = patient.master_patient
    inbound = referral.original_hospital_referral
    ocular = referral.ocular_assessment
    retinal = referral.retinal_report
    brand = resolve_report_branding(referral.encounter, clinic=referral.originating_clinic, report=retinal)
    source = {
        "sources": referral.clinical_sources,
        "ocular_updated_at": ocular.updated_at.isoformat() if ocular else "",
        "retinal_updated_at": retinal.updated_at.isoformat() if retinal else "",
        "ocular_outcome": ocular.management_outcome if ocular else "",
        "retinal_outcome": retinal.urgency_outcome if retinal else "",
        "visual_acuity": (
            f"Left corrected/pinhole: {referral.encounter.left_corrected_pinhole_va or 'Not recorded'}; "
            f"left unaided: {referral.encounter.left_unaided_va or referral.encounter.visual_acuity_left or 'Not recorded'}; "
            f"right corrected/pinhole: {referral.encounter.right_corrected_pinhole_va or 'Not recorded'}; "
            f"right unaided: {referral.encounter.right_unaided_va or referral.encounter.visual_acuity_right or 'Not recorded'}"
        ),
        "iop": f"Left: {referral.encounter.iop_before_dilation_left or 'Not recorded'}; Right: {referral.encounter.iop_before_dilation_right or 'Not recorded'}",
        "dilation": referral.encounter.dilation_drops_used or referral.encounter.dilation_notes,
        "report_status": retinal.report_status if retinal else "",
    }
    version.author_snapshot = {
        "user_id": responsibility.optometrist_id,
        "name": responsibility.clinician_name,
        "role": responsibility.professional_role,
        "registration_number": responsibility.registration_number,
        "clinic": referral.originating_clinic.name,
        "branch": referral.branch.name,
    }
    version.patient_snapshot = {
        "name": f"{patient.first_name} {patient.last_name}".strip(),
        "date_of_birth": str(patient.date_of_birth), "sex": patient.sex,
        "sentinel_patient_id": master.sentinel_patient_id if master else "",
        "encounter_reference": referral.encounter.encounter_id,
        "assessment_date": str(referral.encounter.encounter_date),
        "hospital_mrn": inbound.hospital_mrn if inbound and referral.route == "originating_hospital" else "",
        "original_referral_reference": inbound.referral_id if inbound and referral.route == "originating_hospital" else "",
        "phone": patient.phone if version.include_patient_phone else "",
    }
    version.recipient_snapshot = {
        "organization_id": version.recipient_organization_id,
        "organization_name": version.recipient_organization.name if version.recipient_organization else "",
        "department": version.recipient_department,
    }
    version.clinical_source_snapshot = source
    version.branding_snapshot = {
        "policy": brand.policy,
        "primary_name": getattr(brand.primary_organization, "name", "") or "Sentinel",
        "brand_names": [item.name for item in brand.brands],
        "powered_by_sentinel": brand.powered_by_sentinel,
    }


def render_draft_preview(*, user, referral):
    require_current_author(user, referral)
    version = referral.current_version
    if not version or version.status != OnwardReferralVersion.Status.DRAFT:
        raise ValidationError("Only draft versions use preview.")
    responsibility = EncounterResponsibleOptometrist.objects.get(
        encounter=referral.encounter
    )
    if version.responsible_optometrist_id != responsibility.optometrist_id:
        raise ValidationError("The draft author is stale after a responsibility takeover.")
    _snapshots(referral, version, responsibility)
    return render_onward_referral(version, draft=True)


@transaction.atomic
def finalize_referral(*, user, referral):
    referral = OnwardReferral.objects.select_for_update().select_related(
        "current_version", "encounter", "patient", "patient__master_patient",
        "originating_clinic", "branch", "original_hospital_referral",
        "retinal_report", "ocular_assessment",
    ).get(pk=referral.pk)
    version = OnwardReferralVersion.objects.select_for_update().select_related("recipient_organization").get(pk=referral.current_version_id)
    if version.status == "finalized":
        return version
    if version.status != "draft":
        raise ValidationError("Only a draft version can be finalized.")
    require_current_author(user, referral)
    responsibility = EncounterResponsibleOptometrist.objects.select_for_update().get(encounter=referral.encounter)
    if version.responsible_optometrist_id != responsibility.optometrist_id:
        raise ValidationError("The draft author is stale after a responsibility takeover.")
    validate_draft(version)
    minimum = _minimum_urgency(referral)
    if URGENCY_RANK[version.urgency] < URGENCY_RANK[minimum]:
        raise ValidationError(f"Urgency cannot be lower than the professional {minimum} outcome.")
    if referral.route != "clinic_download" and not version.recipient_organization_id:
        raise ValidationError("A registered recipient organization is required.")
    if version.include_patient_phone and not (referral.patient.phone or "").strip():
        raise ValidationError("No verified patient phone is available to include.")
    if version.urgency == "emergency":
        if not (version.emergency_escalation_confirmed and version.emergency_escalation_method and version.emergency_escalation_note):
            raise ValidationError("Confirm the immediate emergency escalation action before finalization.")
        version.emergency_escalation_by = user
        version.emergency_escalation_at = version.emergency_escalation_at or timezone.now()
    _snapshots(referral, version, responsibility)
    version.finalized_by = user
    version.finalized_at = timezone.now()
    version.pdf_generated_at = version.finalized_at
    pdf = render_onward_referral(version)
    checksum = hashlib.sha256(pdf).hexdigest()
    key = f"clinical-documents/onward-referrals/{referral.referral_uuid}/v{version.version_number}.pdf"
    storage = get_private_clinical_storage()
    created_object = False
    try:
        if storage.exists(key):
            with storage.open(key, "rb") as existing:
                if hashlib.sha256(existing.read()).hexdigest() != checksum:
                    raise ValidationError("A conflicting finalized document already exists.")
        else:
            saved = storage.save(key, ContentFile(pdf))
            created_object = True
            if saved != key:
                storage.delete(saved)
                raise ValidationError("The private document key could not be reserved safely.")
        version.pdf_object_key = key
        version.pdf_checksum_sha256 = checksum
        version.pdf_size = len(pdf)
        version.status = "finalized"
        version.save()
        if version.supersedes_id:
            previous = OnwardReferralVersion.objects.select_for_update().get(pk=version.supersedes_id)
            previous.status = "superseded"
            previous.save(update_fields=["status", "updated_at"])
            previous.availabilities.filter(state="active").update(state="superseded")
        referral.lifecycle = "finalized"
        referral.save(update_fields=["lifecycle", "updated_at"])
        OnwardReferralEvent.objects.create(
            referral=referral, version=version, event_type="finalized", actor=user,
            safe_note="Onward-referral version finalized and electronically signed.",
        )
    except Exception:
        if created_object:
            try:
                storage.delete(key)
            except Exception:
                pass
        raise
    return version


def source_is_stale(version):
    snapshot = version.clinical_source_snapshot or {}
    referral = version.referral
    ocular = referral.ocular_assessment
    retinal = referral.retinal_report
    if ocular and snapshot.get("ocular_updated_at") != ocular.updated_at.isoformat():
        return True
    if retinal and (
        snapshot.get("retinal_updated_at") != retinal.updated_at.isoformat()
        or retinal.report_status in {"returned_to_clinic", "ops_rejected"}
    ):
        return True
    return False


@transaction.atomic
def supersede_referral(*, user, referral, reason):
    referral = OnwardReferral.objects.select_for_update().select_related("current_version", "encounter").get(pk=referral.pk)
    require_current_author(user, referral)
    previous = referral.current_version
    reason = (reason or "").strip()
    if not previous or previous.status != "finalized" or not reason:
        raise ValidationError("A finalized version and amendment reason are required.")
    new = OnwardReferralVersion.objects.create(
        referral=referral, version_number=previous.version_number + 1,
        urgency=previous.urgency, referral_reason=previous.referral_reason,
        requested_specialist_action=previous.requested_specialist_action,
        relevant_history=previous.relevant_history,
        pertinent_findings=previous.pertinent_findings,
        professional_impression=previous.professional_impression,
        management_provided=previous.management_provided,
        include_patient_phone=previous.include_patient_phone,
        recipient_organization=previous.recipient_organization,
        recipient_department=previous.recipient_department,
        responsible_optometrist=user, supersedes=previous,
        amendment_reason=reason, created_by=user,
    )
    referral.current_version = new
    referral.lifecycle = "draft"
    referral.save(update_fields=["current_version", "lifecycle", "updated_at"])
    OnwardReferralEvent.objects.create(
        referral=referral, version=new, event_type="superseding_draft_created",
        actor=user, safe_note="A superseding draft was created.",
        metadata={"superseded_version": previous.version_number, "reason_recorded": True},
    )
    return new


@transaction.atomic
def make_available(*, user, referral, idempotency_key):
    referral = OnwardReferral.objects.select_for_update().select_related("current_version", "encounter").get(pk=referral.pk)
    if not can_distribute(user, referral):
        raise PermissionDenied("Exact onward-referral distribution authority is required.")
    version = referral.current_version
    if not version or version.status != "finalized" or not version.recipient_organization_id:
        raise ValidationError("A finalized version with a registered recipient is required.")
    if source_is_stale(version):
        raise ValidationError(
            "The professional source changed or was returned; create a superseding version before distribution."
        )
    key = (idempotency_key or f"availability:{version.pk}").strip()[:100]
    availability, created = OnwardReferralAvailability.objects.get_or_create(
        version=version, recipient_organization=version.recipient_organization,
        defaults={"idempotency_key": key, "granted_by": user},
    )
    if availability.state != "active":
        raise ValidationError("This version is no longer active for distribution.")
    if created:
        OnwardReferralEvent.objects.create(
            referral=referral, version=version, event_type="made_available",
            actor=user, safe_note="Finalized onward-referral version made available to its registered recipient.",
            metadata={"recipient_organization_id": version.recipient_organization_id},
        )
        notify_organization(
            organization=version.recipient_organization,
            notification_type="onward_referral_available",
            title="Onward referral ready to download",
            message=f"Onward referral {referral.referral_reference} is available securely in Sentinel.",
            action_path=f"/hospital/onward-referrals/{referral.referral_uuid}",
            deduplication_key=f"onward-referral:{version.pk}:available",
            entity_type="onward_referral", entity_id=str(referral.referral_uuid),
        )
    return availability
