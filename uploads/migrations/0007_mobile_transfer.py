import django.core.validators
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0012_screeningencounter_service_branch"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("uploads", "0006_datasetlabel_corrected_visual_acuity_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="MobileTransferSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("session_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("token_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("status", models.CharField(choices=[("open", "Open"), ("completed", "Completed"), ("expired", "Expired"), ("cancelled", "Cancelled")], default="open", max_length=20)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("encounter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mobile_transfer_sessions", to="encounters.screeningencounter")),
                ("initiated_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="initiated_mobile_transfers", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="PendingMobileImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image_file", models.ImageField(upload_to="mobile_transfer_pending/", validators=[django.core.validators.FileExtensionValidator(["jpg", "jpeg", "png"])])),
                ("original_filename", models.CharField(max_length=255)),
                ("checksum_sha256", models.CharField(max_length=64)),
                ("status", models.CharField(choices=[("pending", "Pending review"), ("confirmed", "Confirmed"), ("rejected", "Rejected")], default="pending", max_length=20)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_upload", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="mobile_transfer_source", to="uploads.imageupload")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_mobile_images", to=settings.AUTH_USER_MODEL)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pending_images", to="uploads.mobiletransfersession")),
            ],
            options={"ordering": ["uploaded_at"]},
        ),
        migrations.AddConstraint(
            model_name="pendingmobileimage",
            constraint=models.UniqueConstraint(fields=("session", "checksum_sha256"), name="unique_mobile_image_per_session"),
        ),
    ]
