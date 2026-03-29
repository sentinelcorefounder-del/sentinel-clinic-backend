from rest_framework import serializers
from .models import ImageUpload


class ImageUploadSerializer(serializers.ModelSerializer):
    image_file = serializers.ImageField(use_url=True)

    class Meta:
        model = ImageUpload
        fields = [
            "id",
            "image_upload_id",
            "encounter",
            "patient",
            "eye_laterality",
            "image_type",
            "image_file",
            "image_quality",
            "gradable",
            "retake_required",
            "uploaded_at",
        ]