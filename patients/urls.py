from django.urls import path
from .views import PatientListCreateView, PatientDetailView, PatientSyncView, ClinicDirectPatientCreateView

urlpatterns = [
    path("", PatientListCreateView.as_view(), name="patient-list-create"),
    path("clinic-direct/", ClinicDirectPatientCreateView.as_view(), name="clinic-direct-patient-create"),
    path("sync/", PatientSyncView.as_view(), name="patient-sync"),
    path("<int:pk>/", PatientDetailView.as_view(), name="patient-detail"),
]