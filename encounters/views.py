from django.db.models import Q
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied

from common.tenant import get_user_organization
from .models import ScreeningEncounter
from .serializers import ScreeningEncounterSerializer


class ScreeningEncounterListCreateView(generics.ListCreateAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        queryset = ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
        ).all()

        user = self.request.user
        if not user.is_superuser:
            org = get_user_organization(user)
            if not org:
                return ScreeningEncounter.objects.none()
            queryset = queryset.filter(patient__assigned_clinic=org)

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

        return queryset

    def perform_create(self, serializer):
        user = self.request.user

        if user.is_superuser:
            serializer.save()
            return

        org = get_user_organization(user)
        if not org:
            raise PermissionDenied("You are not linked to a clinic organization.")

        patient = serializer.validated_data.get("patient")
        if not patient:
            raise PermissionDenied("A patient is required.")

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot create encounters for patients outside your clinic.")

        serializer.save()


class ScreeningEncounterDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        queryset = ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
        ).all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ScreeningEncounter.objects.none()

        return queryset.filter(patient__assigned_clinic=org)

    def perform_update(self, serializer):
        user = self.request.user

        if user.is_superuser:
            serializer.save()
            return

        org = get_user_organization(user)
        if not org:
            raise PermissionDenied("You are not linked to a clinic organization.")

        patient = serializer.validated_data.get("patient", serializer.instance.patient)
        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot update encounters outside your clinic.")

        serializer.save()


class PatientEncounterListView(generics.ListAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        queryset = ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
        ).filter(patient_id=patient_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ScreeningEncounter.objects.none()

        return queryset.filter(patient__assigned_clinic=org)