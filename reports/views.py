from rest_framework import generics
from .models import StructuredReport
from .serializers import StructuredReportSerializer
from .permissions import CanManageReports


class StructuredReportListCreateView(generics.ListCreateAPIView):
    queryset = StructuredReport.objects.all()
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]


class StructuredReportDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = StructuredReport.objects.all()
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]


class EncounterReportListView(generics.ListAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        return StructuredReport.objects.filter(encounter_id=encounter_id)


class PatientReportListView(generics.ListAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        return StructuredReport.objects.filter(patient_id=patient_id)