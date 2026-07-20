from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0003_organization_wallet_and_ledger"),
        ("organizations", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SettlementBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("approved", "Approved"), ("paid", "Paid"), ("cancelled", "Cancelled")], default="draft", max_length=20)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("total_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("external_reference", models.CharField(blank=True, default="", max_length=120)),
                ("notes", models.TextField(blank=True, default="")),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_finance_settlements", to=settings.AUTH_USER_MODEL)),
                ("beneficiary_organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="finance_settlement_batches", to="organizations.organization")),
            ],
            options={"ordering": ["-period_end", "-created_at"]},
        ),
        migrations.CreateModel(
            name="SettlementItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("allocation", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="settlement_item", to="finance.encounterallocation")),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="items", to="finance.settlementbatch")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddIndex(model_name="settlementbatch", index=models.Index(fields=["beneficiary_organization", "status"], name="fin_set_org_status_idx")),
    ]
