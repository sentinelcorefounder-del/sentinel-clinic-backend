from django.urls import path
from .views import (
    ImageUploadListCreateView,
    ImageUploadDetailView,
    EncounterImageUploadListView,
)

urlpatterns = [
    path("", ImageUploadListCreateView.as_view(), name="image-upload-list-create"),
    path("<int:pk>/", ImageUploadDetailView.as_view(), name="image-upload-detail"),
    path("encounter/<int:encounter_id>/", EncounterImageUploadListView.as_view(), name="encounter-image-uploads"),
]