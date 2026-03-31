from django.db import models


class Organization(models.Model):
    clinic_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    contact_email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.clinic_id})"