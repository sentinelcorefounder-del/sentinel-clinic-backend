from django.db import migrations


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="clinic_owner_optometrist")


def remove_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="clinic_owner_optometrist").delete()


class Migration(migrations.Migration):
    dependencies = [("users", "0005_clinicalprofessionalprofile")]
    operations = [migrations.RunPython(create_group, remove_group)]
