from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0007_ocular_investigations_ai_review"),
        ("finance", "0010_finance_action_controls"),
    ]

    operations = [
        migrations.AddField(
            model_name="ocularaireview",
            name="fee_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=4000,
                max_digits=14,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="fee_currency",
            field=models.CharField(default="NGN", max_length=3),
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="payment_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("charged", "Charged"),
                    ("refunded", "Refunded"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="charge_ledger_entry",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ocular_ai_review_charge",
                to="finance.walletledgerentry",
            ),
        ),
        migrations.AddField(
            model_name="ocularaireview",
            name="refund_ledger_entry",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ocular_ai_review_refund",
                to="finance.walletledgerentry",
            ),
        ),
        migrations.AddConstraint(
            model_name="ocularaireview",
            constraint=models.UniqueConstraint(
                fields=("encounter",),
                name="encounter_one_ocular_ai_review",
            ),
        ),
    ]
