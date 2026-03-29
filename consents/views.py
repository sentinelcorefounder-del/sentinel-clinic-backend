from rest_framework import generics
from .models import ConsentRecord
from .serializers import ConsentRecordSerializer
from .permissions import CanManageConsents


class ConsentRecordListCreateView(generics.ListCreateAPIView):
    queryset = ConsentRecord.objects.all()
    serializer_class = ConsentRecordSerializer
    permission_classes = [CanManageConsents]


class ConsentRecordDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = ConsentRecord.objects.all()
    serializer_class = ConsentRecordSerializer
    permission_classes = [CanManageConsents]


class EncounterConsentListView(generics.ListAPIView):
    serializer_class = ConsentRecordSerializer
    permission_classes = [CanManageConsents]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        return ConsentRecord.objects.filter(encounter_id=encounter_id)


class PatientConsentListView(generics.ListAPIView):
    serializer_class = ConsentRecordSerializer
    permission_classes = [CanManageConsents]

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        return ConsentRecord.objects.filter(patient_id=patient_id)