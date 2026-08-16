from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from encounters.models import ScreeningEncounter
from organizations.models import Organization
from organizations.services.branches import accessible_branch_ids
from uploads.storage import get_private_clinical_storage

from .models import OnwardReferral, OnwardReferralAccessEvent, OnwardReferralEvent
from .permissions import (
    clinic_can_view, clinic_can_view_encounter, clinical_capabilities,
    hospital_can_view, is_clinic_admin, require_current_author, role_names,
)
from .serializers import OnwardReferralSerializer, ResponsibilitySerializer
from .services import (
    accept_responsibility, create_referral, eligibility, finalize_referral,
    make_available, render_draft_preview, supersede_referral, validate_draft,
)


def _referral(uuid_value):
    return get_object_or_404(
        OnwardReferral.objects.select_related(
            "patient", "patient__master_patient", "encounter",
            "originating_clinic", "branch", "original_hospital_referral",
            "retinal_report", "ocular_assessment", "current_version",
            "current_version__recipient_organization",
        ).prefetch_related("versions", "versions__recipient_organization", "events", "events__actor"),
        referral_uuid=uuid_value,
    )


def _can_view(user, referral, version=None):
    if clinic_can_view(user, referral):
        return True
    if version:
        return hospital_can_view(user, version)
    return any(hospital_can_view(user, item) for item in referral.versions.all())


def _serialized(request, value, *, many=False):
    return OnwardReferralSerializer(value, many=many, context={"request": request}).data


class ResponsibilityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related(
                "patient", "patient__assigned_clinic", "patient__assigned_branch",
                "service_branch",
            ),
            pk=encounter_id,
        )
        if not clinic_can_view_encounter(request.user, encounter):
            raise PermissionDenied("Clinic access is required.")
        value = getattr(encounter, "onward_responsibility", None)
        return Response(ResponsibilitySerializer(value).data if value else None)

    def post(self, request, encounter_id):
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related(
                "patient", "patient__assigned_clinic", "patient__assigned_branch", "service_branch"
            ), pk=encounter_id,
        )
        value = accept_responsibility(
            user=request.user, encounter=encounter,
            clinician_name=request.data.get("clinician_name"),
            professional_role=request.data.get("professional_role"),
            registration_number=request.data.get("registration_number"),
            reason=request.data.get("reason", ""),
        )
        return Response(ResponsibilitySerializer(value).data)


class EligibilityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related("patient", "patient__assigned_clinic", "service_branch"),
            pk=encounter_id,
        )
        if not clinic_can_view_encounter(request.user, encounter):
            raise PermissionDenied("Clinic access is required.")
        result = eligibility(encounter)
        branch = encounter.service_branch or encounter.patient.assigned_branch
        capabilities = clinical_capabilities(
            request.user, clinic=encounter.patient.assigned_clinic, branch=branch,
            responsibility=result["responsible_optometrist"],
        )
        return Response({
            "eligible": result["eligible"],
            "encounter_completed": result["encounter_completed"],
            "eligible_sources": result["eligible_sources"],
            "responsibility": ResponsibilitySerializer(result["responsible_optometrist"]).data if result["responsible_optometrist"] else None,
            "capabilities": capabilities,
        })


class RegisteredHospitalListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = get_user_organization(request.user)
        if request.user.is_superuser or not org or org.organization_type != "clinic" or not (role_names(request.user) & {"optometrist", "reviewer", "clinic_admin"}):
            raise PermissionDenied("Clinic authority is required.")
        return Response(list(Organization.objects.filter(
            organization_type="hospital", is_active=True
        ).order_by("name").values("id", "name", "clinic_id")))


class OnwardReferralListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = get_user_organization(request.user)
        if not org or request.user.is_superuser:
            raise PermissionDenied("Organization-scoped access is required.")
        queryset = OnwardReferral.objects.select_related(
            "patient", "encounter", "originating_clinic", "branch", "current_version",
            "current_version__recipient_organization", "original_hospital_referral",
        ).prefetch_related("versions", "events", "events__actor")
        if org.organization_type == "clinic" and role_names(request.user) & {"optometrist", "reviewer", "clinic_admin"}:
            queryset = queryset.filter(originating_clinic=org)
            branch_ids = accessible_branch_ids(request.user, org)
            if branch_ids is not None:
                queryset = queryset.filter(branch_id__in=branch_ids)
        elif org.organization_type == "hospital" and "hospital_admin" in role_names(request.user):
            queryset = queryset.filter(versions__availabilities__recipient_organization=org).distinct()
        else:
            raise PermissionDenied("Onward-referral access is not assigned to this role.")
        return Response(_serialized(request, queryset, many=True))

    def post(self, request):
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related(
                "patient", "patient__assigned_clinic", "service_branch", "hospital_referral",
                "hospital_referral__source_hospital",
            ), pk=request.data.get("encounter"),
        )
        referral = create_referral(
            user=request.user, encounter=encounter,
            clinical_sources=request.data.get("clinical_sources", []),
            route=request.data.get("route"),
            recipient_organization_id=request.data.get("recipient_organization"),
            recipient_department=request.data.get("recipient_department", ""),
            data=request.data,
        )
        return Response(_serialized(request, _referral(referral.referral_uuid)), status=status.HTTP_201_CREATED)


class OnwardReferralDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, referral_uuid):
        referral = _referral(referral_uuid)
        if not _can_view(request.user, referral):
            raise PermissionDenied("You cannot access this onward referral.")
        organization = get_user_organization(request.user)
        viewed_version = referral.current_version
        if organization.organization_type == "hospital":
            viewed_version = referral.versions.filter(
                availabilities__recipient_organization=organization,
                availabilities__state__in={"active", "superseded"},
            ).order_by("-version_number").first()
        OnwardReferralAccessEvent.objects.create(
            version=viewed_version, actor=request.user,
            organization=organization, action="view",
        )
        return Response(_serialized(request, referral))

    @transaction.atomic
    def patch(self, request, referral_uuid):
        referral = OnwardReferral.objects.select_for_update().select_related("current_version", "encounter", "originating_clinic", "branch").get(referral_uuid=referral_uuid)
        version = referral.current_version
        if not version or version.status != "draft":
            raise ValidationError("Only a draft version can be edited.")
        author = False
        try:
            require_current_author(request.user, referral)
            author = True
        except PermissionDenied:
            if not is_clinic_admin(request.user, referral.originating_clinic, referral.branch):
                raise
        clinical_fields = {
            "urgency", "referral_reason", "requested_specialist_action",
            "relevant_history", "pertinent_findings", "professional_impression",
            "management_provided", "include_patient_phone",
            "emergency_escalation_confirmed", "emergency_escalation_method",
            "emergency_escalation_note",
        }
        recipient_fields = {"recipient_organization", "recipient_department"}
        supplied = set(request.data.keys())
        if not author and supplied - recipient_fields:
            raise PermissionDenied("Clinic administrators may update recipient details only.")
        for field in supplied & clinical_fields:
            setattr(version, field, request.data[field])
        if "recipient_department" in request.data:
            version.recipient_department = (request.data.get("recipient_department") or "").strip()
        if "recipient_organization" in request.data:
            if referral.route == "originating_hospital":
                expected = referral.original_hospital_referral.source_hospital_id
                try:
                    recipient_id = int(request.data["recipient_organization"])
                except (TypeError, ValueError):
                    raise ValidationError("Select the originating hospital.")
                if recipient_id != expected:
                    raise ValidationError("The originating hospital recipient cannot be replaced.")
                version.recipient_organization_id = expected
            elif referral.route == "registered_hospital":
                hospital = Organization.objects.filter(pk=request.data["recipient_organization"], organization_type="hospital", is_active=True).first()
                if not hospital:
                    raise ValidationError("Select an active registered hospital.")
                version.recipient_organization = hospital
            else:
                raise ValidationError("Clinic-download referrals do not grant portal access.")
        if version.emergency_escalation_confirmed:
            if not author:
                raise PermissionDenied("Only the responsible clinical professional may confirm emergency escalation.")
            version.emergency_escalation_by = request.user
            version.emergency_escalation_at = timezone.now()
        validate_draft(version)
        version.save()
        OnwardReferralEvent.objects.create(
            referral=referral, version=version, event_type="draft_updated",
            actor=request.user, safe_note="Onward-referral draft updated.",
            metadata={"updated_fields": sorted(supplied & (clinical_fields | recipient_fields))},
        )
        return Response(_serialized(request, _referral(referral_uuid)))


class FinalizeView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, referral_uuid):
        referral = _referral(referral_uuid)
        finalize_referral(user=request.user, referral=referral)
        return Response(_serialized(request, _referral(referral_uuid)))


class SupersedeView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, referral_uuid):
        referral = _referral(referral_uuid)
        supersede_referral(user=request.user, referral=referral, reason=request.data.get("reason"))
        return Response(_serialized(request, _referral(referral_uuid)))


class VoidView(APIView):
    permission_classes = [IsAuthenticated]
    @transaction.atomic
    def post(self, request, referral_uuid):
        referral = OnwardReferral.objects.select_for_update().select_related("current_version", "encounter").get(referral_uuid=referral_uuid)
        require_current_author(request.user, referral)
        version = referral.current_version
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError("A withdrawal reason is required.")
        if version.availabilities.filter(state="active").exists():
            raise ValidationError("A distributed version cannot be unilaterally voided; create a superseding correction.")
        version.status = "voided"; version.void_reason = reason; version.save(update_fields=["status", "void_reason", "updated_at"])
        referral.lifecycle = "voided"; referral.save(update_fields=["lifecycle", "updated_at"])
        OnwardReferralEvent.objects.create(referral=referral, version=version, event_type="voided", actor=request.user, safe_note="Onward-referral version withdrawn with a recorded reason.")
        return Response(_serialized(request, _referral(referral_uuid)))


class AvailabilityView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, referral_uuid):
        referral = _referral(referral_uuid)
        value = make_available(user=request.user, referral=referral, idempotency_key=request.headers.get("Idempotency-Key") or request.data.get("idempotency_key"))
        return Response({"state": value.state, "granted_at": value.granted_at})


class PreviewView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, referral_uuid):
        referral = _referral(referral_uuid)
        if not clinic_can_view(request.user, referral):
            raise PermissionDenied("Clinic access is required for draft preview.")
        response = HttpResponse(
            render_draft_preview(user=request.user, referral=referral),
            content_type="application/pdf",
        )
        response["Cache-Control"] = "private, no-store, max-age=0"; response["Pragma"] = "no-cache"; response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = f'inline; filename="{referral.referral_reference}-draft.pdf"'
        return response


class DocumentView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, referral_uuid, version_number):
        referral = _referral(referral_uuid)
        version = get_object_or_404(referral.versions.select_related("recipient_organization"), version_number=version_number)
        if version.status not in {"finalized", "superseded"} or not version.pdf_object_key:
            raise ValidationError("This finalized document is unavailable.")
        if not (clinic_can_view(request.user, referral) or hospital_can_view(request.user, version)):
            raise PermissionDenied("You cannot access this document.")
        file_obj = get_private_clinical_storage().open(version.pdf_object_key, "rb")
        org = get_user_organization(request.user)
        OnwardReferralAccessEvent.objects.create(version=version, actor=request.user, organization=org, action="download")
        response = FileResponse(file_obj, content_type="application/pdf")
        response["Cache-Control"] = "private, no-store, max-age=0"; response["Pragma"] = "no-cache"; response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = f'attachment; filename="{referral.referral_reference}-v{version.version_number}.pdf"'
        return response
