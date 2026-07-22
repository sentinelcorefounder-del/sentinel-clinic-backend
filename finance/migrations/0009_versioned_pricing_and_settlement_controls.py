from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0008_service_allowance_control"),
    ]

    operations = [
        migrations.AddField(
            model_name="pricingrule",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="pricingrule",
            name="supersedes",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="superseded_by", to="finance.pricingrule",
            ),
        ),
        migrations.AddField(
            model_name="settlementbatch",
            name="payment_evidence",
            field=models.FileField(blank=True, null=True, upload_to="finance/settlements/%Y/%m/"),
        ),
        migrations.AddField(
            model_name="settlementbatch",
            name="paid_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="paid_finance_settlements", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="settlementbatch",
            name="cancelled_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="cancelled_finance_settlements", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="settlementbatch",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="settlementbatch",
            name="cancellation_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="settlementitem",
            name="allocation",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="settlement_items",
                to="finance.encounterallocation",
            ),
        ),
        migrations.AddConstraint(
            model_name="pricingrule",
            constraint=models.UniqueConstraint(
                fields=("contract", "name", "version"),
                name="fin_unique_pricing_rule_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="settlementbatch",
            constraint=models.UniqueConstraint(
                condition=~models.Q(external_reference=""),
                fields=("external_reference",),
                name="fin_unique_settlement_external_ref",
            ),
        ),
    ]
