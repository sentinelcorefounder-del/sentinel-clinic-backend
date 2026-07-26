from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("consents", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="consentrecord",
            name="consent_type",
            field=models.CharField(
                choices=[
                    ("care_delivery", "Care Delivery"),
                    ("data_sharing", "Data Sharing"),
                    ("ai_clinical_review", "AI Clinical Review"),
                    ("ai_training", "AI Training"),
                    ("research_use", "Research Use"),
                ],
                max_length=30,
            ),
        ),
    ]
