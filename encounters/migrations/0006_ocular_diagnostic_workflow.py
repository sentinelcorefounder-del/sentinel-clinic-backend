from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0005_screeningencounter_source_overridden_at_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="screeningencounter",
            name="programme",
            field=models.CharField(
                choices=[
                    ("diabetic_screening", "Diabetic Retinal Assessment"),
                    ("ocular_diagnostics", "General Ocular Assessment"),
                    ("combined_assessment", "Combined Diabetic and Ocular Assessment"),
                ],
                default="diabetic_screening",
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name="OcularDiagnosticAssessment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fundus_photography_performed", models.BooleanField(default=False)),
                ("visual_field_performed", models.BooleanField(default=False)),
                ("tonometry_performed", models.BooleanField(default=False)),
                ("visual_acuity_performed", models.BooleanField(default=True)),
                ("anterior_eye_assessment_performed", models.BooleanField(default=False)),
                ("presenting_complaint", models.TextField(blank=True, default="")),
                ("ocular_history", models.TextField(blank=True, default="")),
                ("anterior_eye_findings", models.TextField(blank=True, default="")),
                ("fundus_findings", models.TextField(blank=True, default="")),
                ("visual_field_summary", models.TextField(blank=True, default="")),
                ("tonometry_summary", models.TextField(blank=True, default="")),
                ("impression", models.TextField(blank=True, default="")),
                ("management_plan", models.TextField(blank=True, default="")),
                ("management_outcome", models.CharField(blank=True, choices=[("routine", "Routine care"), ("monitor", "Monitor / review"), ("refer_routine", "Routine referral"), ("refer_urgent", "Urgent referral"), ("refer_emergency", "Emergency referral")], default="", max_length=30)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("completed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="completed_ocular_assessments", to=settings.AUTH_USER_MODEL)),
                ("encounter", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="ocular_assessment", to="encounters.screeningencounter")),
            ],
        ),
    ]
