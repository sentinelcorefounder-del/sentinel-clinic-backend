from django.urls import path
from .views import (
    ConsentRecordListCreateView,
    ConsentRecordDetailView,
    EncounterConsentListView,
    PatientConsentListView,
)

urlpatterns = [
    path("", ConsentRecordListCreateView.as_view(), name="consent-list-create"),
    path("<int:pk>/", ConsentRecordDetailView.as_view(), name="consent-detail"),
    path("encounter/<int:encounter_id>/", EncounterConsentListView.as_view(), name="encounter-consents"),
    path("patient/<int:patient_id>/", PatientConsentListView.as_view(), name="patient-consents"),
]