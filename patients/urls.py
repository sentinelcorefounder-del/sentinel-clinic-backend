from django.urls import path
from .views import PatientListCreateView, PatientDetailView, PatientSyncView

urlpatterns = [
    path("", PatientListCreateView.as_view(), name="patient-list-create"),
    path("sync/", PatientSyncView.as_view(), name="patient-sync"),
    path("<int:pk>/", PatientDetailView.as_view(), name="patient-detail"),
]