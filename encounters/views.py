from django.db.models import Q
from rest_framework import generics
from .models import ScreeningEncounter
from .serializers import ScreeningEncounterSerializer


class ScreeningEncounterListCreateView(generics.ListCreateAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        queryset = ScreeningEncounter.objects.all()

        search = self.request.query_params.get("search")
        status_value = self.request.query_params.get("status")
        encounter_date = self.request.query_params.get("date")

        if search:
            queryset = queryset.filter(
                Q(encounter_id__icontains=search) |
                Q(patient__patient_id__icontains=search) |
                Q(patient__first_name__icontains=search) |
                Q(patient__last_name__icontains=search)
            )

        if status_value:
            queryset = queryset.filter(screening_status=status_value)

        if encounter_date:
            queryset = queryset.filter(encounter_date=encounter_date)

        return queryset


class ScreeningEncounterDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = ScreeningEncounter.objects.all()
    serializer_class = ScreeningEncounterSerializer


class PatientEncounterListView(generics.ListAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        return ScreeningEncounter.objects.filter(patient_id=patient_id)