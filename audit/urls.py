from django.urls import path

from .views import PatientTimelineView

urlpatterns = [
    path(
        "patients/<int:patient_id>/timeline/",
        PatientTimelineView.as_view(),
        name="patient-timeline",
    ),
]
