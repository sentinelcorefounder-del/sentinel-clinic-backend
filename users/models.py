from django.db import models
from django.contrib.auth.models import User
from organizations.models import Organization


class UserOrganization(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="organization_link",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="user_links",
    )

    def __str__(self):
        return f"{self.user.username} -> {self.organization.clinic_id}"