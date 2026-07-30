from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def assign_existing_users(apps, schema_editor):
    UserOrganization = apps.get_model("users", "UserOrganization")
    Access = apps.get_model("users", "UserBranchAccess")
    Branch = apps.get_model("organizations", "OrganizationBranch")
    for link in UserOrganization.objects.all().iterator():
        branch = Branch.objects.filter(
            organization_id=link.organization_id,
            is_head_office=True,
        ).first()
        if branch:
            Access.objects.get_or_create(
                user_id=link.user_id,
                branch_id=branch.id,
                defaults={"has_all_branch_access": True, "is_default": True},
            )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("organizations", "0006_organizationbranch"),
        ("users", "0002_usersecurityprofile"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserBranchAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("has_all_branch_access", models.BooleanField(default=False)),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="user_access", to="organizations.organizationbranch")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="branch_access", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="userbranchaccess",
            constraint=models.UniqueConstraint(fields=("user", "branch"), name="unique_user_branch_access"),
        ),
        migrations.RunPython(assign_existing_users, migrations.RunPython.noop),
    ]
