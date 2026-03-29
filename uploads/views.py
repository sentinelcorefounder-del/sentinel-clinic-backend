from rest_framework import generics
from .models import ImageUpload
from .serializers import ImageUploadSerializer
from .permissions import CanManageUploads


class ImageUploadListCreateView(generics.ListCreateAPIView):
    queryset = ImageUpload.objects.all()
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]


class ImageUploadDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = ImageUpload.objects.all()
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]


class EncounterImageUploadListView(generics.ListAPIView):
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        return ImageUpload.objects.filter(encounter_id=encounter_id)