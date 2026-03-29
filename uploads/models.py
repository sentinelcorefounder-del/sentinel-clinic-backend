from django.db import models
from patients.models import Patient
from encounters.models import ScreeningEncounter


class ImageUpload(models.Model):
    LATERALITY_CHOICES = [
        ("left", "Left"),
        ("right", "Right"),
    ]

    IMAGE_TYPE_CHOICES = [
        ("fundus", "Fundus"),
        ("oct", "OCT"),
        ("other", "Other"),
    ]

    IMAGE_QUALITY_CHOICES = [
        ("good", "Good"),
        ("acceptable", "Acceptable"),
        ("poor", "Poor"),
        ("ungradable", "Ungradable"),
    ]

    image_upload_id = models.CharField(max_length=30, unique=True)
    encounter = models.ForeignKey(
        ScreeningEncounter,
        on_delete=models.CASCADE,
        related_name="image_uploads"
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name="image_uploads"
    )
    eye_laterality = models.CharField(max_length=10, choices=LATERALITY_CHOICES)
    image_type = models.CharField(max_length=20, choices=IMAGE_TYPE_CHOICES, default="fundus")
    image_file = models.ImageField(upload_to="encounter_uploads/")
    image_quality = models.CharField(max_length=20, choices=IMAGE_QUALITY_CHOICES, default="good")
    gradable = models.BooleanField(default=True)
    retake_required = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.image_upload_id} - {self.encounter.encounter_id}"
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.encounter.update_status_from_related_records()    