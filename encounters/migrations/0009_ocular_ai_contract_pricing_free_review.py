from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0008_ocular_ai_review_billing"),
        ("finance", "0010_finance_action_controls"),
    ]

    operations = [
        migrations.AddField(
            model_name="ocularaireview",
            name="pricing_rule",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ocular_ai_reviews",
                to="finance.pricingrule",
            ),
        ),
        migrations.AlterField(
            model_name="ocularaireview",
            name="payment_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("charged", "Charged"),
                    ("refunded", "Refunded"),
                    ("free", "Free clinic review"),
                    ("free_failed", "Free review failed"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
