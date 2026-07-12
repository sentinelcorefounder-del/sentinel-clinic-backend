import os

from django.db.models import Q
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from organizations.models import Organization
from users.models import UserOrganization

from .models import Patient
from .permissions import CanManagePatients
from .serializers import PatientSerializer, ClinicDirectPatientCreateSerializer
from .sync_serializers import PatientSyncSerializer
from audit.services import record_patient_event
from organizations.models import OrganizationProfile
from django.db import transaction


def get_user_clinic_organization(user):
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


class PatientListCreateView(generics.ListCreateAPIView):
    serializer_class = PatientSerializer
    permission_classes = [CanManagePatients]

    def get_queryset(self):
        user = self.request.user
        org = get_user_clinic_organization(user)

        if not org or org.organization_type != "clinic":
            return Patient.objects.none()

        queryset = (
            Patient.objects.select_related("assigned_clinic")
            .filter(assigned_clinic=org)
        )

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(patient_id__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(phone__icontains=search)
            )

        return queryset.order_by("-created_at")

    def perform_create(self, serializer):
        raise PermissionDenied(
            "Patients are created via Sentinel Ops, not manually."
        )


class PatientDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PatientSerializer
    permission_classes = [CanManagePatients]

    def get_queryset(self):
        user = self.request.user
        org = get_user_clinic_organization(user)

        if not org or org.organization_type != "clinic":
            return Patient.objects.none()

        return Patient.objects.select_related("assigned_clinic").filter(
            assigned_clinic=org
        )

    def perform_update(self, serializer):
        user = self.request.user
        org = get_user_clinic_organization(user)

        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        patient = self.get_object()

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot update this patient.")

        updated_patient = serializer.save()
        record_patient_event(
            patient=updated_patient,
            event_key=f"patient:{updated_patient.pk}:manual_update:{updated_patient.updated_at}",
            category="registration",
            event_type="patient_updated",
            title="Patient record updated",
            description="Patient details were updated by a clinic user.",
            source_type="patient",
            source_id=updated_patient.pk,
            actor=user,
            organization=org,
            occurred_at=updated_patient.updated_at,
        )

    def perform_destroy(self, instance):
        user = self.request.user
        org = get_user_clinic_organization(user)

        if not org or instance.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot delete this patient.")

        instance.delete()


def generate_clinic_patient_id(clinic):
    prefix = f"{(clinic.clinic_id or 'CLINIC').upper().replace(' ', '-')}-PAT-"
    latest = Patient.objects.filter(patient_id__startswith=prefix).order_by("-id").first()
    try:
        number = int(latest.patient_id.split("-")[-1]) + 1 if latest else 1
    except Exception:
        number = (latest.id + 1) if latest else 1
    candidate = f"{prefix}{number:06d}"
    while Patient.objects.filter(patient_id=candidate).exists():
        number += 1
        candidate = f"{prefix}{number:06d}"
    return candidate


class ClinicDirectPatientCreateView(APIView):
    permission_classes = [CanManagePatients]

    @transaction.atomic
    def post(self, request):
        user = request.user
        org = get_user_clinic_organization(user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        profile, _ = OrganizationProfile.objects.get_or_create(organization=org)
        if not profile.clinic_direct_screening_enabled:
            raise PermissionDenied("Clinic-direct diabetic retinal assessment is not enabled for this clinic.")
        if not profile.can_create_direct_patients:
            raise PermissionDenied("This clinic is not permitted to create direct patient records.")

        serializer = ClinicDirectPatientCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        patient = serializer.save(
            patient_id=generate_clinic_patient_id(org),
            assigned_clinic=org, consent_status="pending",
            referral_id="", referral_status="clinic_direct",
            source_system="clinic_direct",
        )
        record_patient_event(
            patient=patient, event_key=f"patient:{patient.pk}:clinic_direct_created",
            category="registration", event_type="clinic_direct_patient_created",
            title="Clinic-direct patient registered",
            description=f"Patient registered directly by {org.name} for diabetic retinal assessment.",
            source_type="patient", source_id=patient.pk, actor=user, organization=org,
            visibility="clinic_ops", metadata={"source_type":"clinic_direct"},
            occurred_at=patient.created_at,
        )
        return Response(PatientSerializer(patient).data, status=status.HTTP_201_CREATED)


class PatientSyncView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        token = request.headers.get("X-SENTINEL-SYNC-TOKEN")
        expected = os.environ.get("SENTINEL_SYNC_TOKEN", "")

        if not expected or token != expected:
            return Response(
                {"detail": "Unauthorized"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = PatientSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            clinic = Organization.objects.get(clinic_id=data["assigned_clinic_id"])
        except Organization.DoesNotExist:
            return Response(
                {"detail": "Assigned clinic not found in clinic portal"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        patient, created = Patient.objects.update_or_create(
            patient_id=data["patient_id"],
            defaults={
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "date_of_birth": data["date_of_birth"],
                "sex": data["sex"],
                "phone": data.get("phone", ""),
                "email": data.get("email", ""),
                "address": data.get("address", ""),
                "city": data.get("city", ""),
                "state": data.get("state", ""),
                "country": data.get("country", "Nigeria"),
                "consent_status": data.get("consent_status", "pending"),
                "assigned_clinic": clinic,
                "referral_id": data.get("referral_id", ""),
                "referral_status": data.get("referral_status", ""),
                "appointment_date": data.get("appointment_date"),
                "source_system": "sentinel_ops",
            },
        )

        return Response(
            {
                "detail": "Patient synced successfully",
                "patient_id": patient.patient_id,
                "assigned_clinic": clinic.clinic_id,
                "created": created,
            },
            status=status.HTTP_200_OK,
        )