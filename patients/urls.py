from django.urls import path
from .views import PatientListCreateView, PatientDetailView, PatientSyncView, ClinicDirectPatientCreateView
from .access_views import (
    ClinicHistoricalAccessRequestListCreateView,
    ClinicHistoricalRecordView,
)

urlpatterns = [
    path("", PatientListCreateView.as_view(), name="patient-list-create"),
    path("clinic-direct/", ClinicDirectPatientCreateView.as_view(), name="clinic-direct-patient-create"),
    path("sync/", PatientSyncView.as_view(), name="patient-sync"),
    path(
        "historical-access/",
        ClinicHistoricalAccessRequestListCreateView.as_view(),
        name="clinic-historical-access",
    ),
    path(
        "historical-access/<int:pk>/records/",
        ClinicHistoricalRecordView.as_view(),
        name="clinic-historical-records",
    ),
    path("<int:pk>/", PatientDetailView.as_view(), name="patient-detail"),
]