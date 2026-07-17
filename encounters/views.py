from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.services import record_patient_event
from common.tenant import get_user_organization
from organizations.models import OrganizationProfile
from referrals.models import HospitalReferral
from users.models import UserOrganization

from .models import ScreeningEncounter
from .serializers import ScreeningEncounterSerializer


CLOSED_REFERRAL_STATUSES = {"completed", "cancelled"}


def get_user_clinic(user):
    org = get_user_organization(user)
    if org:
        return org

    user_org = (
        UserOrganization.objects.select_related("organization")
        .filter(user=user)
        .first()
    )
    return user_org.organization if user_org else None


def active_referrals_for_patient(patient, clinic):
    return (
        HospitalReferral.objects.select_related(
            "source_hospital",
            "matched_clinic",
            "patient",
        )
        .filter(patient=patient, matched_clinic=clinic)
        .exclude(referral_status__in=CLOSED_REFERRAL_STATUSES)
        .order_by("-referral_date", "-created_at", "-id")
    )


def user_can_override_source(user):
    if user.is_superuser:
        return True
    roles = set(user.groups.values_list("name", flat=True))
    return bool({"clinic_admin", "reviewer", "optometrist"} & roles)


class PatientActiveReferralListView(APIView):
    def get(self, request, patient_id):
        org = get_user_clinic(request.user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        from patients.models import Patient

        patient = Patient.objects.filter(
            pk=patient_id,
            assigned_clinic=org,
        ).first()
        if not patient:
            return Response(
                {"detail": "Patient not found in this clinic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        referrals = active_referrals_for_patient(patient, org)
        return Response({
            "patient_id": patient.id,
            "sentinel_patient_id": patient.patient_id,
            "active_referrals": [
                {
                    "id": referral.id,
                    "referral_id": referral.referral_id,
                    "referral_status": referral.referral_status,
                    "referral_date": referral.referral_date,
                    "reason_for_referral": referral.reason_for_referral or "",
                    "hospital_mrn": referral.hospital_mrn or "",
                    "source_hospital_id": referral.source_hospital_id,
                    "source_hospital_name": (
                        referral.source_hospital.name
                        if referral.source_hospital
                        else ""
                    ),
                }
                for referral in referrals
            ],
            "clinic_direct_override_allowed": user_can_override_source(
                request.user
            ),
        })


class ScreeningEncounterListCreateView(generics.ListCreateAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            return ScreeningEncounter.objects.none()

        queryset = ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "hospital_referral",
            "hospital_referral__source_hospital",
        ).filter(patient__assigned_clinic=org)

        search = self.request.query_params.get("search")
        status_value = self.request.query_params.get("status")
        encounter_date = self.request.query_params.get("date")

        if search:
            queryset = queryset.filter(
                Q(encounter_id__icontains=search)
                | Q(patient__patient_id__icontains=search)
                | Q(patient__first_name__icontains=search)
                | Q(patient__last_name__icontains=search)
                | Q(hospital_referral__referral_id__icontains=search)
                | Q(hospital_referral__source_hospital__name__icontains=search)
            )
        if status_value:
            queryset = queryset.filter(screening_status=status_value)
        if encounter_date:
            queryset = queryset.filter(encounter_date=encounter_date)

        return queryset.order_by("-created_at")

    @transaction.atomic
    def perform_create(self, serializer):
        user = self.request.user
        org = get_user_clinic(user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        patient = serializer.validated_data.get("patient")
        if not patient:
            raise PermissionDenied("A patient is required.")
        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied(
                "You cannot create encounters outside your clinic."
            )

        profile, _ = OrganizationProfile.objects.get_or_create(
            organization=org
        )
        requested_source = (
            serializer.validated_data.get("source_type") or ""
        ).strip()
        selected_referral = serializer.validated_data.get(
            "hospital_referral"
        )
        override_reason = (
            serializer.validated_data.get("source_override_reason") or ""
        ).strip()
        active_referrals = list(
            active_referrals_for_patient(patient, org)
        )

        if requested_source == "hospital_referral":
            referral = selected_referral
            if referral is None:
                if len(active_referrals) == 1:
                    referral = active_referrals[0]
                elif len(active_referrals) > 1:
                    raise PermissionDenied(
                        "This patient has more than one active hospital "
                        "referral. Select the exact referral."
                    )
                else:
                    raise PermissionDenied(
                        "No active hospital referral is available for "
                        "this patient and clinic."
                    )

            if referral.patient_id != patient.id:
                raise PermissionDenied(
                    "The selected referral belongs to a different patient."
                )
            if referral.matched_clinic_id != org.id:
                raise PermissionDenied(
                    "The selected referral is not assigned to this clinic."
                )
            if referral.referral_status in CLOSED_REFERRAL_STATUSES:
                raise PermissionDenied(
                    "The selected referral is closed or cancelled."
                )

            values = {
                "originating_organization": org,
                "programme": "diabetic_screening",
                "source_type": "hospital_referral",
                "workflow_route": "sentinel_managed",
                "payment_responsibility": "hospital",
                "hospital_referral": referral,
                "source_override_reason": "",
                "source_overridden_by": None,
                "source_overridden_at": None,
            }

        elif requested_source == "clinic_direct":
            if not profile.clinic_direct_screening_enabled:
                raise PermissionDenied(
                    "Clinic-direct diabetic retinal assessment is not "
                    "enabled for this clinic."
                )

            if active_referrals:
                if not override_reason:
                    raise PermissionDenied(
                        "This patient has an active hospital referral. "
                        "Continue under it or provide a reason for a "
                        "separate clinic-direct episode."
                    )
                if not user_can_override_source(user):
                    raise PermissionDenied(
                        "Only an authorised clinic clinician or "
                        "administrator can override an active referral."
                    )

            requested_route = (
                serializer.validated_data.get("workflow_route") or ""
            )
            if profile.workflow_mode == "sentinel_managed":
                route = "sentinel_managed"
            elif profile.workflow_mode == "clinic_managed":
                route = "clinic_managed"
            elif requested_route in {
                "clinic_managed",
                "sentinel_managed",
            }:
                route = requested_route
            else:
                raise PermissionDenied(
                    "Hybrid clinics must select a workflow."
                )

            values = {
                "originating_organization": org,
                "programme": "diabetic_screening",
                "source_type": "clinic_direct",
                "workflow_route": route,
                "payment_responsibility": (
                    serializer.validated_data.get(
                        "payment_responsibility"
                    )
                    or profile.default_payment_responsibility
                ),
                "hospital_referral": None,
                "source_override_reason": (
                    override_reason if active_referrals else ""
                ),
                "source_overridden_by": (
                    user if active_referrals else None
                ),
                "source_overridden_at": (
                    timezone.now() if active_referrals else None
                ),
            }
        else:
            raise PermissionDenied(
                "Choose either a hospital referral or a clinic-direct "
                "assessment pathway."
            )

        encounter = serializer.save(**values)

        if active_referrals and requested_source == "clinic_direct":
            record_patient_event(
                patient=patient,
                event_key=f"encounter:{encounter.pk}:source_override",
                category="encounter",
                event_type="encounter_source_overridden",
                title="Active referral bypassed",
                description=(
                    "An authorised clinic user created a separate "
                    "clinic-direct episode despite an active referral."
                ),
                source_type="encounter",
                source_id=encounter.pk,
                encounter_id=encounter.encounter_id,
                actor=user,
                organization=org,
                visibility="clinic_ops",
                metadata={
                    "override_reason": override_reason,
                    "active_referral_ids": [
                        referral.referral_id
                        for referral in active_referrals
                    ],
                },
                occurred_at=encounter.source_overridden_at,
            )

        record_patient_event(
            patient=patient,
            event_key=f"encounter:{encounter.pk}:created",
            category="encounter",
            event_type="screening_encounter_created",
            title="Diabetic retinal assessment created",
            description=(
                f"{encounter.get_source_type_display()} encounter "
                f"{encounter.encounter_id} was created."
            ),
            source_type="encounter",
            source_id=encounter.pk,
            encounter_id=encounter.encounter_id,
            referral_id=(
                encounter.hospital_referral.referral_id
                if encounter.hospital_referral
                else ""
            ),
            actor=user,
            organization=org,
            visibility="all" if encounter.hospital_referral else "clinic_ops",
            metadata={
                "source_type": encounter.source_type,
                "workflow_route": encounter.workflow_route,
                "payment_responsibility": encounter.payment_responsibility,
                "source_override_reason": encounter.source_override_reason,
            },
            occurred_at=encounter.created_at,
        )


class ScreeningEncounterDetailView(
    generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            return ScreeningEncounter.objects.none()

        return ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "hospital_referral",
            "hospital_referral__source_hospital",
        ).filter(patient__assigned_clinic=org)

    def perform_update(self, serializer):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        patient = serializer.validated_data.get(
            "patient", serializer.instance.patient
        )
        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied(
                "You cannot update encounters outside your clinic."
            )
        serializer.save()


class PatientEncounterListView(generics.ListAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            return ScreeningEncounter.objects.none()

        return ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "hospital_referral",
            "hospital_referral__source_hospital",
        ).filter(
            patient_id=self.kwargs["patient_id"],
            patient__assigned_clinic=org,
        ).order_by("-created_at")
