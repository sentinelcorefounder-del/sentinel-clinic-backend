from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("encounters", "0016_screeningencounter_is_diabetic"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="report_status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("awaiting_ops", "Awaiting Ops review"),
                    ("ops_approved", "Ops approved"),
                    ("returned_to_clinic", "Returned to clinic"),
                    ("issued", "Issued"),
                ],
                default="draft",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="signed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="signed_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="signed_ocular_assessments", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="signer_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="submitted_to_ops_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="submitted_to_ops_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="submitted_ocular_assessments", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="ops_reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="ops_reviewed_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="reviewed_ocular_assessments", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="ops_review_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="issued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="issued_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="issued_ocular_assessments", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="OcularDiagnosticAssessmentVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version_number", models.PositiveIntegerField()),
                ("clinical_snapshot", models.JSONField(default=dict)),
                ("clinician_snapshot", models.JSONField(default=dict)),
                ("checksum_sha256", models.CharField(max_length=64)),
                ("signed_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("assessment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="versions", to="encounters.oculardiagnosticassessment")),
                ("signed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ocular_report_versions_signed", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["assessment_id", "version_number"]},
        ),
        migrations.AddConstraint(
            model_name="oculardiagnosticassessmentversion",
            constraint=models.UniqueConstraint(fields=("assessment", "version_number"), name="ocular_report_unique_version"),
        ),
        migrations.AddField(
            model_name="oculardiagnosticassessment",
            name="current_version",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="current_for_assessments", to="encounters.oculardiagnosticassessmentversion",
            ),
        ),
    ]
