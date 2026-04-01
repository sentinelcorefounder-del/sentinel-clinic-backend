from rest_framework import generics
from rest_framework.exceptions import PermissionDenied

from common.tenant import get_user_organization
from .models import StructuredReport
from .serializers import StructuredReportSerializer
from .permissions import CanManageReports


class StructuredReportListCreateView(generics.ListCreateAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        queryset = StructuredReport.objects.select_related(
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
            return StructuredReport.objects.none()

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

        if not patient or not encounter:
            raise PermissionDenied("Both patient and encounter are required.")

        if encounter.patient_id != patient.id:
            raise PermissionDenied("The selected encounter does not belong to the selected patient.")

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot create reports for another clinic's patient.")

        serializer.save()


class StructuredReportDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        queryset = StructuredReport.objects.select_related(
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
            return StructuredReport.objects.none()

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

        if encounter.patient_id != patient.id:
            raise PermissionDenied("The selected encounter does not belong to the selected patient.")

        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot update reports for another clinic's patient.")

        serializer.save()


class EncounterReportListView(generics.ListAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        queryset = StructuredReport.objects.select_related(
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
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)


class PatientReportListView(generics.ListAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        queryset = StructuredReport.objects.select_related(
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
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)