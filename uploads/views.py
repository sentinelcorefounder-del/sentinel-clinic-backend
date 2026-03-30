from rest_framework import generics, status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .models import ImageUpload
from .serializers import ImageUploadSerializer
from .permissions import CanManageUploads


class ImageUploadListCreateView(generics.ListCreateAPIView):
    queryset = ImageUpload.objects.all()
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]
    parser_classes = [MultiPartParser, FormParser]

    def create(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as exc:
            print("UPLOAD ERROR:", repr(exc))
            return Response(
                {"detail": f"Upload failed: {repr(exc)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ImageUploadDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = ImageUpload.objects.all()
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]
    parser_classes = [MultiPartParser, FormParser]


class EncounterImageUploadListView(generics.ListAPIView):
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        return ImageUpload.objects.filter(encounter_id=encounter_id)