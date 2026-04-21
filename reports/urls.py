from django.urls import path
from .views import (
    StructuredReportListCreateView,
    StructuredReportDetailView,
    EncounterReportListView,
    PatientReportListView,
    StructuredReportPDFView,
    submit_report_to_ops,
    approve_report_by_ops,
    reject_report_by_ops,
)

urlpatterns = [
    path("", StructuredReportListCreateView.as_view(), name="report-list-create"),
    path("<int:pk>/", StructuredReportDetailView.as_view(), name="report-detail"),
    path("<int:pk>/pdf/", StructuredReportPDFView.as_view(), name="report-pdf"),
    path("<int:pk>/submit-to-ops/", submit_report_to_ops, name="report-submit-to-ops"),
    path("<int:pk>/ops-approve/", approve_report_by_ops, name="report-ops-approve"),
    path("<int:pk>/ops-reject/", reject_report_by_ops, name="report-ops-reject"),
    path("encounter/<int:encounter_id>/", EncounterReportListView.as_view(), name="encounter-reports"),
    path("patient/<int:patient_id>/", PatientReportListView.as_view(), name="patient-reports"),
]