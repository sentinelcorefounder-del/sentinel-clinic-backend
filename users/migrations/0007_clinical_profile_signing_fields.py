from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("users", "0006_provision_clinic_owner_optometrist")]

    operations = [
        migrations.AddField(
            model_name="clinicalprofessionalprofile",
            name="registration_body",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="clinicalprofessionalprofile",
            name="signature_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
