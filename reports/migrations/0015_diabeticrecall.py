from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0016_screeningencounter_is_diabetic"),
        ("reports", "0014_historicalreportdocument"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="DiabeticRecall",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("recall_months", models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(24)])),
                ("base_date", models.DateField()),
                ("due_date", models.DateField(db_index=True)),
                ("status", models.CharField(choices=[("scheduled","Scheduled"),("contacted","Contacted"),("booked","Booked"),("completed","Completed"),("deferred","Deferred")], db_index=True, default="scheduled", max_length=20)),
                ("note", models.TextField(blank=True, default="")),
                ("source_type", models.CharField(choices=[("encounter","Encounter"),("structured_report","Structured report"),("historical_report","Historical report")], default="encounter", max_length=30)),
                ("contacted_at", models.DateTimeField(blank=True, null=True)),
                ("booked_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("deferred_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_diabetic_recalls", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_diabetic_recalls", to=settings.AUTH_USER_MODEL)),
                ("encounter", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="diabetic_recall", to="encounters.screeningencounter")),
                ("historical_report", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="diabetic_recalls", to="reports.historicalreportdocument")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="diabetic_recalls", to="organizations.organization")),
                ("patient", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="diabetic_recalls", to="patients.patient")),
            ],
            options={"ordering": ["due_date", "id"]},
        ),
        migrations.AddIndex(model_name="diabeticrecall", index=models.Index(fields=["organization","status","due_date"], name="rpt_diabrec_org_status_due")),
        migrations.AddIndex(model_name="diabeticrecall", index=models.Index(fields=["patient","due_date"], name="rpt_diabrec_patient_due")),
    ]
