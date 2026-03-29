from django.urls import path
from .views import (
    StructuredReportListCreateView,
    StructuredReportDetailView,
    EncounterReportListView,
    PatientReportListView,
)

urlpatterns = [
    path("", StructuredReportListCreateView.as_view(), name="report-list-create"),
    path("<int:pk>/", StructuredReportDetailView.as_view(), name="report-detail"),
    path("encounter/<int:encounter_id>/", EncounterReportListView.as_view(), name="encounter-reports"),
    path("patient/<int:patient_id>/", PatientReportListView.as_view(), name="patient-reports"),
]