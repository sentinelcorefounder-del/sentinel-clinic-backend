from django.urls import path
from .views import (
    ScreeningEncounterListCreateView,
    ScreeningEncounterDetailView,
    PatientEncounterListView,
    PatientActiveReferralListView,
)

urlpatterns = [
    path("", ScreeningEncounterListCreateView.as_view(), name="encounter-list-create"),
    path("<int:pk>/", ScreeningEncounterDetailView.as_view(), name="encounter-detail"),
    path(
        "patient/<int:patient_id>/active-referrals/",
        PatientActiveReferralListView.as_view(),
        name="patient-active-referrals",
    ),
    path(
        "patient/<int:patient_id>/",
        PatientEncounterListView.as_view(),
        name="patient-encounters",
    ),
]