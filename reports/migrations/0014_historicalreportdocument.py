from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion
import reports.models


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0015_screeningencounter_assessment_location_snapshot_and_more"),
        ("patients", "0004_branch_and_safe_sequence"),
        ("referrals", "0007_referral_branches"),
        ("reports", "0013_structuredreport_server_generated_identifier"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HistoricalReportDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("historical_report_id", models.CharField(default=reports.models.generate_historical_report_id, editable=False, max_length=32, unique=True)),
                ("title", models.CharField(default="Historical uploaded report", max_length=180)),
                ("report_date", models.DateField()),
                ("source_organization_name", models.CharField(blank=True, default="", max_length=255)),
                ("source_note", models.TextField(blank=True, default="")),
                ("document", models.FileField(upload_to=reports.models.historical_report_upload_path, validators=[django.core.validators.FileExtensionValidator(allowed_extensions=["pdf"])])),
                ("original_filename", models.CharField(blank=True, default="", max_length=255)),
                ("hospital_visible", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("encounter", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="historical_reports", to="encounters.screeningencounter")),
                ("hospital_referral", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="historical_reports", to="referrals.hospitalreferral")),
                ("patient", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="historical_report_documents", to="patients.patient")),
                ("uploaded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="uploaded_historical_reports", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-report_date", "-created_at"]},
        ),
        migrations.AddIndex(model_name="historicalreportdocument", index=models.Index(fields=["encounter", "report_date"], name="rpt_hist_enc_date_idx")),
        migrations.AddIndex(model_name="historicalreportdocument", index=models.Index(fields=["hospital_referral", "hospital_visible"], name="rpt_hist_ref_vis_idx")),
    ]
