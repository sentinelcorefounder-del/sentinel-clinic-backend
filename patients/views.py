from django.db.models import Q
from rest_framework import generics
from .models import Patient
from .serializers import PatientSerializer
from .permissions import CanManagePatients


class PatientListCreateView(generics.ListCreateAPIView):
    serializer_class = PatientSerializer
    permission_classes = [CanManagePatients]

    def get_queryset(self):
        queryset = Patient.objects.all()
        search = self.request.query_params.get("search")

        if search:
            queryset = queryset.filter(
                Q(patient_id__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(phone__icontains=search)
            )

        return queryset


class PatientDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Patient.objects.all()
    serializer_class = PatientSerializer
    permission_classes = [CanManagePatients]