from django.contrib import admin
from .models import ImageUpload


@admin.register(ImageUpload)
class ImageUploadAdmin(admin.ModelAdmin):
    list_display = (
        "image_upload_id",
        "encounter",
        "patient",
        "eye_laterality",
        "image_type",
        "image_quality",
        "uploaded_at",
    )
    search_fields = ("image_upload_id", "encounter__encounter_id", "patient__patient_id")
    list_filter = ("eye_laterality", "image_type", "image_quality", "gradable", "retake_required")