from django.db import migrations, models
import django.db.models.deletion


def create_main_branches(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Branch = apps.get_model("organizations", "OrganizationBranch")
    for organization in Organization.objects.all().iterator():
        Branch.objects.get_or_create(
            organization=organization,
            branch_code="MAIN",
            defaults={
                "name": "Main Branch",
                "address": organization.address,
                "contact_email": organization.contact_email,
                "phone": organization.phone,
                "is_head_office": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("organizations", "0005_organizationprofile")]

    operations = [
        migrations.CreateModel(
            name="OrganizationBranch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("branch_code", models.CharField(max_length=50)),
                ("name", models.CharField(max_length=255)),
                ("address", models.TextField(blank=True)),
                ("contact_email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=50)),
                ("is_head_office", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("inherits_branding", models.BooleanField(default=True)),
                ("inherits_contract", models.BooleanField(default=True)),
                ("inherits_wallet", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="branches", to="organizations.organization")),
            ],
            options={"ordering": ["organization__name", "name"]},
        ),
        migrations.AddConstraint(
            model_name="organizationbranch",
            constraint=models.UniqueConstraint(fields=("organization", "branch_code"), name="unique_branch_code_per_organization"),
        ),
        migrations.AddConstraint(
            model_name="organizationbranch",
            constraint=models.UniqueConstraint(fields=("organization", "name"), name="unique_branch_name_per_organization"),
        ),
        migrations.RunPython(create_main_branches, migrations.RunPython.noop),
    ]
