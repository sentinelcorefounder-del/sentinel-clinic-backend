import os

from django.db.models import Q
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from organizations.models import Organization
from .models import Patient
from .serializers import PatientSerializer
from .permissions import CanManagePatients
from .sync_serializers import PatientSyncSerializer


class PatientListCreateView(generics.ListCreateAPIView):
    serializer_class = PatientSerializer
    permission_classes = [CanManagePatients]

    def get_queryset(self):
        queryset = Patient.objects.select_related("assigned_clinic").all()

        user = self.request.user
        if not user.is_superuser:
            org = get_user_organization(user)
            if org:
                queryset = queryset.filter(assigned_clinic=org)
            else:
                queryset = queryset.none()

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(patient_id__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(phone__icontains=search)
            )

        return queryset

    def perform_create(self, serializer):
        user = self.request.user

        if user.is_superuser:
            serializer.save()
            return

        raise PermissionDenied(
            "Patients should be created from Sentinel Ops sync, not manually in the clinic portal."
        )


class PatientDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PatientSerializer
    permission_classes = [CanManagePatients]

    def get_queryset(self):
        queryset = Patient.objects.select_related("assigned_clinic").all()

        user = self.request.user
        if not user.is_superuser:
            org = get_user_organization(user)
            if org:
                queryset = queryset.filter(assigned_clinic=org)
            else:
                queryset = queryset.none()

        return queryset

    def perform_update(self, serializer):
        user = self.request.user

        if user.is_superuser:
            serializer.save()
            return

        org = get_user_organization(user)
        if not org:
            raise PermissionDenied("You are not linked to a clinic organization.")

        patient = self.get_object()
        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot update a patient outside your clinic.")

        new_assigned_clinic = serializer.validated_data.get("assigned_clinic")
        if new_assigned_clinic and new_assigned_clinic.id != org.id:
            raise PermissionDenied("You cannot reassign a patient to another clinic.")

        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user

        if user.is_superuser:
            instance.delete()
            return

        org = get_user_organization(user)
        if not org or instance.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot delete a patient outside your clinic.")

        instance.delete()


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