from rest_framework import generics
from rest_framework.exceptions import PermissionDenied

from common.tenant import get_user_organization
from .models import ConsentRecord
from .serializers import ConsentRecordSerializer
from .permissions import CanManageConsents


class ConsentRecordListCreateView(generics.ListCreateAPIView):
    serializer_class = ConsentRecordSerializer
    permission_classes = [CanManageConsents]

    def get_queryset(self):
        queryset = ConsentRecord.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
        ).all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ConsentRecord.objects.none()

        return queryset.filter(patient__assigned_clinic=org)

    def perform_create(self, serializer):
        user = self.request.user

        if user.is_superuser:
            serializer.save()
            return

        org = get_user_organization(user)
        if not org:
            raise PermissionDenied("You are not linked to a clinic organization.")

        patient = serializer.validated_data.get("patient")
        encounter = serializer.validated_data.get("encounter")

        if not patient:
            raise PermissionDenied("A patient is required.")

        if encounter and encounter.patient_id != patient.id:
            raise PermissionDenied("The selected encounter does not belong to the selected patient.")

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot record consent for another clinic's patient.")

        serializer.save()


class ConsentRecordDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ConsentRecordSerializer
    permission_classes = [CanManageConsents]

    def get_queryset(self):
        queryset = ConsentRecord.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
        ).all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ConsentRecord.objects.none()

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
        encounter = serializer.validated_data.get("encounter", serializer.instance.encounter)

        if encounter and encounter.patient_id != patient.id:
            raise PermissionDenied("The selected encounter does not belong to the selected patient.")

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot update consent for another clinic's patient.")

        serializer.save()


class EncounterConsentListView(generics.ListAPIView):
    serializer_class = ConsentRecordSerializer
    permission_classes = [CanManageConsents]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        queryset = ConsentRecord.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
        ).filter(encounter_id=encounter_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ConsentRecord.objects.none()

        return queryset.filter(patient__assigned_clinic=org)


class PatientConsentListView(generics.ListAPIView):
    serializer_class = ConsentRecordSerializer
    permission_classes = [CanManageConsents]

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        queryset = ConsentRecord.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
        ).filter(patient_id=patient_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ConsentRecord.objects.none()

        return queryset.filter(patient__assigned_clinic=org)