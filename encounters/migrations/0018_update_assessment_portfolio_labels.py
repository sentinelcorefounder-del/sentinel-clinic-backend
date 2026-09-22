from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0017_ocular_report_lifecycle"),
    ]

    operations = [
        migrations.AlterField(
            model_name="screeningencounter",
            name="service_package",
            field=models.CharField(
                blank=True,
                choices=[
                    ("diabetic_retinal_assessment", "Diabetic Retinal Assessment"),
                    ("eye_health_screening", "Retinal and Glaucoma-Risk Assessment"),
                    ("combined_diabetic_eye_health", "Combined Diabetic Retinal and Glaucoma-Risk Assessment"),
                    ("comprehensive_ocular_assessment", "Comprehensive Ocular Assessment"),
                ],
                help_text="Authoritative clinical service package. Historical unclassified ocular episodes remain blank.",
                max_length=50,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="screeningencounter",
            name="programme",
            field=models.CharField(
                choices=[
                    ("diabetic_screening", "Diabetic Retinal Assessment"),
                    ("eye_health_screening", "Retinal and Glaucoma-Risk Assessment"),
                    ("ocular_diagnostics", "Comprehensive Ocular Assessment"),
                    ("combined_assessment", "Combined Diabetic Retinal and Glaucoma-Risk Assessment"),
                ],
                default="diabetic_screening",
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="encounterservicepackageevent",
            name="new_package",
            field=models.CharField(
                choices=[
                    ("diabetic_retinal_assessment", "Diabetic Retinal Assessment"),
                    ("eye_health_screening", "Retinal and Glaucoma-Risk Assessment"),
                    ("combined_diabetic_eye_health", "Combined Diabetic Retinal and Glaucoma-Risk Assessment"),
                    ("comprehensive_ocular_assessment", "Comprehensive Ocular Assessment"),
                ],
                max_length=50,
            ),
        ),
    ]
