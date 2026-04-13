from django.db import models


class Organization(models.Model):
    clinic_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    contact_email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    logo = models.ImageField(upload_to="clinic_logos/", blank=True, null=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)

    report_signatory_name = models.CharField(max_length=255, blank=True)
    report_signatory_title = models.CharField(max_length=255, blank=True)
    report_signatory_odorbn = models.CharField(max_length=100, blank=True)
    report_footer_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.clinic_id})"