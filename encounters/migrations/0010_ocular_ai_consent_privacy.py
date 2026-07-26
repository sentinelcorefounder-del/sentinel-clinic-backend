import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("consents", "0002_add_ai_clinical_review_consent"),
        ("encounters", "0009_ocular_ai_contract_pricing_free_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="ocularaireview",
            name="clinical_ai_consent",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="ocular_clinical_ai_reviews", to="consents.consentrecord",
            ),
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="training_consent",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="ocular_training_ai_reviews", to="consents.consentrecord",
            ),
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="consent_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="privacy_verified_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="privacy_verified_ocular_ai_reviews", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="privacy_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="deidentified_review_reference",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="transmitted_data_manifest",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
