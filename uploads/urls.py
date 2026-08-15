from django.urls import path
from .views import (
    ImageUploadListCreateView,
    ImageUploadDetailView,
    EncounterImageUploadListView,
    PatientImageUploadListView,
    PatientImageComparisonView,
    DatasetTrainingExportView,
    MobileTransferCreateView,
    MobileTransferPublicView,
    MobileTransferReviewView,
    MobileTransferImageReviewView,
)
from .bulk_views import (
    BulkImageImportConfirmView,
    BulkImageImportCreateView,
    BulkImageImportDetailView,
    BulkImageImportGroupResolveView,
    BulkImageImportEncounterSearchView,
    BulkImageImportPreviewView,
    BulkImageImportSessionListView,
)
from .asset_views import ImageUploadContentView

urlpatterns = [
    path("<int:pk>/content/", ImageUploadContentView.as_view(), name="image-upload-content"),
    path("bulk-imports/", BulkImageImportCreateView.as_view(), name="bulk-import-create"),
    path("bulk-imports/sessions/", BulkImageImportSessionListView.as_view(), name="bulk-import-sessions"),
    path("bulk-imports/<uuid:import_id>/", BulkImageImportDetailView.as_view(), name="bulk-import-detail"),
    path("bulk-imports/<uuid:import_id>/groups/<uuid:group_id>/", BulkImageImportGroupResolveView.as_view(), name="bulk-import-group-resolve"),
    path("bulk-imports/<uuid:import_id>/encounters/", BulkImageImportEncounterSearchView.as_view(), name="bulk-import-encounter-search"),
    path("bulk-imports/<uuid:import_id>/items/<uuid:item_id>/preview/", BulkImageImportPreviewView.as_view(), name="bulk-import-preview"),
    path("bulk-imports/<uuid:import_id>/confirm/", BulkImageImportConfirmView.as_view(), name="bulk-import-confirm"),
    path("mobile-transfer/encounter/<int:encounter_id>/", MobileTransferCreateView.as_view(), name="mobile-transfer-create"),
    path("mobile-transfer/public/<str:token>/", MobileTransferPublicView.as_view(), name="mobile-transfer-public"),
    path("mobile-transfer/<uuid:session_id>/", MobileTransferReviewView.as_view(), name="mobile-transfer-review"),
    path("mobile-transfer/<uuid:session_id>/images/<int:image_id>/review/", MobileTransferImageReviewView.as_view(), name="mobile-transfer-image-review"),
    path("", ImageUploadListCreateView.as_view(), name="image-upload-list-create"),
    path("<int:pk>/", ImageUploadDetailView.as_view(), name="image-upload-detail"),
    path("encounter/<int:encounter_id>/", EncounterImageUploadListView.as_view(), name="encounter-image-uploads"),
    path("patient/<int:patient_id>/", PatientImageUploadListView.as_view(), name="patient-image-uploads"),
    path("patient/<int:patient_id>/comparison/", PatientImageComparisonView.as_view(), name="patient-image-comparison"),
    path("dataset/training-export/", DatasetTrainingExportView.as_view(), name="dataset-training-export"),
]
