from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0006_organizationbranch"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PartnerNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("message", models.TextField(blank=True)),
                ("level", models.CharField(choices=[("info", "Info"), ("success", "Success"), ("warning", "Warning"), ("danger", "Danger")], default="info", max_length=20)),
                ("notification_type", models.CharField(max_length=80)),
                ("action_path", models.CharField(blank=True, max_length=255)),
                ("entity_type", models.CharField(blank=True, max_length=80)),
                ("entity_id", models.CharField(blank=True, max_length=120)),
                ("deduplication_key", models.CharField(max_length=255)),
                ("is_read", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="partner_notifications", to="organizations.organization")),
                ("recipient", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="partner_notifications", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="partnernotification",
            constraint=models.UniqueConstraint(fields=("recipient", "deduplication_key"), name="unique_partner_notification_per_recipient"),
        ),
    ]
