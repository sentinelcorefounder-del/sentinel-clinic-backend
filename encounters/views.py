from django.db.models import Q
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied

from common.tenant import get_user_organization
from users.models import UserOrganization

from .models import ScreeningEncounter
from .serializers import ScreeningEncounterSerializer
from organizations.models import OrganizationProfile
from referrals.models import HospitalReferral
from audit.services import record_patient_event


def get_user_clinic(user):
    org = get_user_organization(user)

    if org:
        return org

    user_org = (
        UserOrganization.objects.select_related("organization")
        .filter(user=user)
        .first()
    )

    if user_org:
        return user_org.organization

    return None


class ScreeningEncounterListCreateView(generics.ListCreateAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        user = self.request.user
        org = get_user_clinic(user)

        if not org or org.organization_type != "clinic":
            return ScreeningEncounter.objects.none()

        queryset = ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
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
            )

        if status_value:
            queryset = queryset.filter(screening_status=status_value)

        if encounter_date:
            queryset = queryset.filter(encounter_date=encounter_date)

        return queryset.order_by("-created_at")

    def perform_create(self, serializer):
        user = self.request.user
        org = get_user_clinic(user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        patient = serializer.validated_data.get("patient")
        if not patient:
            raise PermissionDenied("A patient is required.")
        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot create encounters outside your clinic.")

        profile, _ = OrganizationProfile.objects.get_or_create(organization=org)
        source_type = serializer.validated_data.get("source_type") or "hospital_referral"
        values = {"originating_organization": org, "programme": "diabetic_screening"}

        if source_type == "clinic_direct":
            if not profile.clinic_direct_screening_enabled:
                raise PermissionDenied("Clinic-direct diabetic retinal assessment is not enabled for this clinic.")
            requested = serializer.validated_data.get("workflow_route") or ""
            if profile.workflow_mode == "sentinel_managed": route = "sentinel_managed"
            elif profile.workflow_mode == "clinic_managed": route = "clinic_managed"
            elif requested in {"clinic_managed", "sentinel_managed"}: route = requested
            else: raise PermissionDenied("Hybrid clinics must select a workflow for this screening.")
            values.update(source_type="clinic_direct", workflow_route=route, hospital_referral=None, payment_responsibility=serializer.validated_data.get("payment_responsibility") or profile.default_payment_responsibility)
        else:
            referral = serializer.validated_data.get("hospital_referral") or HospitalReferral.objects.filter(patient=patient, matched_clinic=org).exclude(referral_status__in=["completed", "cancelled"]).order_by("-created_at").first()
            if not referral:
                raise PermissionDenied("A hospital referral is required for a hospital-referred encounter.")
            values.update(source_type="hospital_referral", workflow_route="sentinel_managed", payment_responsibility="hospital", hospital_referral=referral)

        encounter = serializer.save(**values)
        record_patient_event(
            patient=patient, event_key=f"encounter:{encounter.pk}:created",
            category="encounter", event_type="screening_encounter_created",
            title="Diabetic retinal assessment created",
            description=f"{encounter.get_source_type_display()} encounter {encounter.encounter_id} was created.",
            source_type="encounter", source_id=encounter.pk, encounter_id=encounter.encounter_id,
            referral_id=encounter.hospital_referral.referral_id if encounter.hospital_referral else "",
            actor=user, organization=org, visibility="all" if encounter.hospital_referral else "clinic_ops",
            metadata={"source_type":encounter.source_type,"workflow_route":encounter.workflow_route,"payment_responsibility":encounter.payment_responsibility},
            occurred_at=encounter.created_at,
        )


class ScreeningEncounterDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        user = self.request.user
        org = get_user_clinic(user)

        if not org or org.organization_type != "clinic":
            return ScreeningEncounter.objects.none()

        return ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
        ).filter(patient__assigned_clinic=org)

    def perform_update(self, serializer):
        user = self.request.user
        org = get_user_clinic(user)

        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        patient = serializer.validated_data.get("patient", serializer.instance.patient)

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot update encounters outside your clinic.")

        serializer.save()


class PatientEncounterListView(generics.ListAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        user = self.request.user
        org = get_user_clinic(user)

        if not org or org.organization_type != "clinic":
            return ScreeningEncounter.objects.none()

        return ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
        ).filter(
            patient_id=patient_id,
            patient__assigned_clinic=org,
        ).order_by("-created_at")