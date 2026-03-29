from django.urls import path
from .views import (
    ScreeningEncounterListCreateView,
    ScreeningEncounterDetailView,
    PatientEncounterListView,
)

urlpatterns = [
    path("", ScreeningEncounterListCreateView.as_view(), name="encounter-list-create"),
    path("<int:pk>/", ScreeningEncounterDetailView.as_view(), name="encounter-detail"),
    path("patient/<int:patient_id>/", PatientEncounterListView.as_view(), name="patient-encounters"),
]