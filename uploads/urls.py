from django.urls import path
from .views import (
    ImageUploadListCreateView,
    ImageUploadDetailView,
    EncounterImageUploadListView,
    DatasetLabelListCreateView,
    DatasetLabelDetailView,
    DatasetLabelExportView,
)

urlpatterns = [
    path("", ImageUploadListCreateView.as_view(), name="image-upload-list-create"),
    path("<int:pk>/", ImageUploadDetailView.as_view(), name="image-upload-detail"),
    path("encounter/<int:encounter_id>/", EncounterImageUploadListView.as_view(), name="encounter-image-uploads"),

    path("dataset-labels/", DatasetLabelListCreateView.as_view(), name="dataset-label-list-create"),
    path("dataset-labels/<int:pk>/", DatasetLabelDetailView.as_view(), name="dataset-label-detail"),
    path("dataset-labels/export/", DatasetLabelExportView.as_view(), name="dataset-label-export"),
]