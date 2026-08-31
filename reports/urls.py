from django.urls import path
from .delivery_views import (
    PatientDeliveryListCreateView,
    PatientDeliverySendView,
)
from .recall_views import (
    RecallActionView,
    RecallQueueView,
)
from .views import (
    StructuredReportListCreateView,
    StructuredReportDetailView,
    EncounterReportListView,
    PatientReportListView,
    ClinicReportListView,
    StructuredReportPDFView,
    submit_report_to_ops,
    approve_report_by_ops,
    reject_report_by_ops,
    clinic_issue_report,
    EyeHealthScreeningReportView,
    EyeHealthScreeningPreviewView,
    EyeHealthScreeningFinalizeView,
    EyeHealthScreeningCorrectionView,
    EyeHealthScreeningPDFView,
    CombinedScreeningBundleView,
)

urlpatterns = [
    path(
        "patient-deliveries/",
        PatientDeliveryListCreateView.as_view(),
        name="patient-delivery-list-create",
    ),
    path(
        "patient-deliveries/<int:pk>/send/",
        PatientDeliverySendView.as_view(),
        name="patient-delivery-send",
    ),
    path(
        "recalls/",
        RecallQueueView.as_view(),
        name="recall-queue",
    ),
    path(
        "recalls/<int:pk>/action/",
        RecallActionView.as_view(),
        name="recall-action",
    ),

    path("", StructuredReportListCreateView.as_view(), name="report-list-create"),
    path("<int:pk>/", StructuredReportDetailView.as_view(), name="report-detail"),
    path("<int:pk>/pdf/", StructuredReportPDFView.as_view(), name="report-pdf"),
    path("eye-health/encounter/<int:encounter_id>/", EyeHealthScreeningReportView.as_view(), name="eye-health-report"),
    path("eye-health/<int:pk>/preview/", EyeHealthScreeningPreviewView.as_view(), name="eye-health-preview"),
    path("eye-health/<int:pk>/finalize/", EyeHealthScreeningFinalizeView.as_view(), name="eye-health-finalize"),
    path("eye-health/<int:pk>/correction/", EyeHealthScreeningCorrectionView.as_view(), name="eye-health-correction"),
    path("eye-health/<int:pk>/pdf/", EyeHealthScreeningPDFView.as_view(), name="eye-health-pdf"),
    path("eye-health/combined/<int:encounter_id>/bundle/", CombinedScreeningBundleView.as_view(), name="combined-screening-bundle"),
    path("<int:pk>/submit-to-ops/", submit_report_to_ops, name="report-submit-to-ops"),
    path("<int:pk>/clinic-issue/", clinic_issue_report, name="report-clinic-issue"),
    path("<int:pk>/ops-approve/", approve_report_by_ops, name="report-ops-approve"),
    path("<int:pk>/ops-reject/", reject_report_by_ops, name="report-ops-reject"),
    path("encounter/<int:encounter_id>/", EncounterReportListView.as_view(), name="encounter-reports"),
    path("patient/<int:patient_id>/", PatientReportListView.as_view(), name="patient-reports"),
    path("clinic/", ClinicReportListView.as_view(), name="clinic-reports"),
]
