from django.urls import path
from .views import (
    ImageUploadListCreateView,
    ImageUploadDetailView,
    EncounterImageUploadListView,
    PatientImageUploadListView,
    PatientImageComparisonView,
    DatasetTrainingExportView,
)

urlpatterns = [
    path("", ImageUploadListCreateView.as_view(), name="image-upload-list-create"),
    path("<int:pk>/", ImageUploadDetailView.as_view(), name="image-upload-detail"),
    path("encounter/<int:encounter_id>/", EncounterImageUploadListView.as_view(), name="encounter-image-uploads"),
    path("patient/<int:patient_id>/", PatientImageUploadListView.as_view(), name="patient-image-uploads"),
    path("patient/<int:patient_id>/comparison/", PatientImageComparisonView.as_view(), name="patient-image-comparison"),
    path("dataset/training-export/", DatasetTrainingExportView.as_view(), name="dataset-training-export"),
]
