from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reports", "0008_structuredreport_clinical_summary_overridden_and_more")]

    operations = [
        migrations.AddField(
            model_name="structuredreport",
            name="report_layout",
            field=models.CharField(
                choices=[("text_only", "Text only"), ("with_investigations", "With investigations")],
                default="text_only",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="structuredreport",
            name="selected_fundus_upload_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="structuredreport",
            name="selected_ocular_investigation_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="structuredreport",
            name="attachment_captions",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
