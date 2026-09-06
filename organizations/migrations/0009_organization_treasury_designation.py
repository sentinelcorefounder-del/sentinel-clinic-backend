from django.db import migrations, models


def designate_existing_treasury(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    # Preserve the currently funded Project Sentinel identity as treasury.
    # The later controlled split command creates a separate Sentinel Clinic.
    Organization.objects.filter(clinic_id="SNT-CLINIC").update(is_sentinel_treasury=True)


def reverse_designation(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Organization.objects.filter(clinic_id="SNT-CLINIC").update(is_sentinel_treasury=False)


class Migration(migrations.Migration):
    dependencies = [("organizations", "0008_alter_organization_organization_type")]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="is_sentinel_treasury",
            field=models.BooleanField(
                default=False,
                help_text="Protected designation for the single authoritative Project Sentinel treasury organisation.",
            ),
        ),
        migrations.RunPython(designate_existing_treasury, reverse_designation),
        migrations.AddConstraint(
            model_name="organization",
            constraint=models.UniqueConstraint(
                fields=("is_sentinel_treasury",),
                condition=models.Q(is_sentinel_treasury=True),
                name="organizations_single_sentinel_treasury",
            ),
        ),
    ]
