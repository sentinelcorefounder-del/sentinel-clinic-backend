from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("reports", "0015_diabeticrecall"),
    ]

    operations = [
        migrations.AlterField(
            model_name="reportstatusevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("created", "Created"),
                    ("responsibility_accepted", "Clinical responsibility accepted"),
                    ("responsibility_taken_over", "Clinical responsibility taken over"),
                    ("clinician_signed", "Clinician Signed"),
                    ("submitted_to_ops", "Submitted to Ops"),
                    ("returned_to_clinic", "Returned to Clinic"),
                    ("resubmitted", "Resubmitted"),
                    ("rejected", "Rejected"),
                    ("issued", "Issued"),
                    ("clinic_signed", "Clinic Signed"),
                    ("clinic_issued", "Clinic Issued"),
                    ("queued_for_distribution", "Queued for Distribution"),
                    ("released_to_hospital", "Released to Hospital"),
                    ("hospital_viewed", "Hospital Viewed"),
                    ("hospital_downloaded", "Hospital Downloaded"),
                ],
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="review_status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft / not submitted"),
                    ("not_required", "Ops review not required"),
                    ("awaiting_ops", "Awaiting Ops review"),
                    ("approved", "Ops approved"),
                    ("returned_to_clinic", "Returned to clinic"),
                    ("legacy", "Legacy finalized report"),
                ],
                default="legacy",
                max_length=24,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="submitted_to_ops_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="submitted_to_ops_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="eye_health_reports_submitted_to_ops", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="ops_reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="ops_reviewed_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="eye_health_reports_reviewed_by_ops", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="ops_review_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="signed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="signed_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="eye_health_reports_signed", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="issued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="issued_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="eye_health_reports_issued", to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
