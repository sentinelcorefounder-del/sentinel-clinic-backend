from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0010_ocular_ai_consent_privacy"),
    ]

    operations = [
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="report_layout",
            field=models.CharField(
                choices=[
                    ("text_only", "Text only"),
                    ("with_investigations", "With investigations"),
                ],
                default="text_only",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="selected_fundus_upload_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="selected_ocular_investigation_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="attachment_captions",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
