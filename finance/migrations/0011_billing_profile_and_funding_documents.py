from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0010_finance_action_controls"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("legal_entity_name", models.CharField(default="Afriophthalmics", max_length=255)),
                ("trading_name", models.CharField(default="Sentinel", max_length=255)),
                ("registered_address", models.TextField(blank=True, default="")),
                ("company_registration_number", models.CharField(blank=True, default="", max_length=100)),
                ("tax_identification_number", models.CharField(blank=True, default="", max_length=100)),
                ("finance_email", models.EmailField(blank=True, default="", max_length=254)),
                ("finance_phone", models.CharField(blank=True, default="", max_length=60)),
                ("bank_name", models.CharField(blank=True, default="", max_length=180)),
                ("bank_account_name", models.CharField(blank=True, default="", max_length=180)),
                ("bank_account_number", models.CharField(blank=True, default="", max_length=80)),
                ("bank_branch_code", models.CharField(blank=True, default="", max_length=80)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("transfer_instructions", models.TextField(blank=True, default="")),
                ("funding_request_prefix", models.CharField(default="SEN-BT", max_length=20)),
                ("receipt_prefix", models.CharField(default="SEN-RCPT", max_length=20)),
                ("is_active", models.BooleanField(default=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_billing_profiles", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-is_active", "-updated_at"],
                "constraints": [models.UniqueConstraint(condition=models.Q(("is_active", True)), fields=("is_active",), name="fin_single_active_billing_profile")],
            },
        ),
        migrations.AddField(
            model_name="banktransferfundingrequest", name="billing_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="banktransferfundingrequest", name="customer_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="banktransferfundingrequest", name="receipt_reference",
            field=models.CharField(blank=True, max_length=40, null=True, unique=True),
        ),
    ]
