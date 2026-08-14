from django.db import models
from django.contrib.auth.models import User
from organizations.models import Organization, OrganizationBranch


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
        return f"{self.user.username} -> {self.organization.name} ({self.organization.clinic_id})"


class UserSecurityProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="security_profile",
    )
    must_change_password = models.BooleanField(default=False)
    is_internal_sentinel_staff = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} security profile"


class UserBranchAccess(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="branch_access",
    )
    branch = models.ForeignKey(
        OrganizationBranch,
        on_delete=models.CASCADE,
        related_name="user_access",
    )
    has_all_branch_access = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "branch"],
                name="unique_user_branch_access",
            )
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        try:
            organization_id = self.user.organization_link.organization_id
        except Exception:
            organization_id = None
        if organization_id and self.branch.organization_id != organization_id:
            raise ValidationError(
                {"branch": "Branch must belong to the user's organization."}
            )
