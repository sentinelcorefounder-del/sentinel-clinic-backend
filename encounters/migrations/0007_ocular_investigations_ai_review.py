import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0006_ocular_diagnostic_workflow"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OcularInvestigation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("investigation_id", models.CharField(max_length=30, unique=True)),
                ("investigation_type", models.CharField(choices=[("visual_field", "Visual field"), ("fundus", "Fundus photograph"), ("oct", "OCT"), ("anterior_segment", "Anterior segment"), ("other", "Other")], max_length=30)),
                ("laterality", models.CharField(choices=[("left", "Left"), ("right", "Right"), ("both", "Both"), ("not_applicable", "Not applicable")], max_length=20)),
                ("test_type", models.CharField(blank=True, default="", max_length=100)),
                ("device_name", models.CharField(blank=True, default="", max_length=150)),
                ("performed_at", models.DateTimeField(blank=True, null=True)),
                ("reliability", models.CharField(choices=[("reliable", "Reliable"), ("borderline", "Borderline"), ("unreliable", "Unreliable"), ("not_recorded", "Not recorded")], default="not_recorded", max_length=20)),
                ("reliability_notes", models.TextField(blank=True, default="")),
                ("interpretation", models.TextField(blank=True, default="")),
                ("file", models.FileField(upload_to="ocular_investigations/", validators=[django.core.validators.FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])])),
                ("original_filename", models.CharField(blank=True, default="", max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("encounter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ocular_investigations", to="encounters.screeningencounter")),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="uploaded_ocular_investigations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-performed_at", "-uploaded_at"]},
        ),
        migrations.CreateModel(
            name="OcularAIReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("review_id", models.CharField(max_length=30, unique=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("completed", "Completed"), ("failed", "Failed")], default="pending", max_length=20)),
                ("provider", models.CharField(default="hybrid", max_length=30)),
                ("model_version", models.CharField(blank=True, default="", max_length=100)),
                ("clinician_impression_snapshot", models.TextField()),
                ("clinician_management_snapshot", models.TextField()),
                ("suspected_conditions", models.JSONField(blank=True, default=list)),
                ("supporting_findings", models.JSONField(blank=True, default=list)),
                ("differential_diagnoses", models.JSONField(blank=True, default=list)),
                ("suggested_urgency", models.CharField(blank=True, default="", max_length=50)),
                ("suggested_management", models.TextField(blank=True, default="")),
                ("limitations", models.JSONField(blank=True, default=list)),
                ("agreement_status", models.CharField(choices=[("agreement", "Agreement"), ("partial_agreement", "Partial agreement"), ("material_disagreement", "Material disagreement"), ("insufficient_data", "Insufficient data")], default="insufficient_data", max_length=30)),
                ("disagreement_reasons", models.JSONField(blank=True, default=list)),
                ("expert_review_required", models.BooleanField(default=False)),
                ("raw_response_json", models.JSONField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, default="")),
                ("clinician_decision", models.CharField(choices=[("pending", "Pending"), ("accepted", "Accepted"), ("modified", "Modified"), ("rejected", "Rejected")], default="pending", max_length=20)),
                ("clinician_decision_notes", models.TextField(blank=True, default="")),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="decided_ocular_ai_reviews", to=settings.AUTH_USER_MODEL)),
                ("encounter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ocular_ai_reviews", to="encounters.screeningencounter")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="requested_ocular_ai_reviews", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-requested_at"]},
        ),
    ]
