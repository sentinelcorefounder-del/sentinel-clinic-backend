from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("finance", "0003_organization_wallet_and_ledger"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentWebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_key", models.CharField(max_length=180, unique=True)),
                ("event_name", models.CharField(max_length=80)),
                ("reference", models.CharField(blank=True, default="", max_length=120)),
                ("payload", models.JSONField(default=dict)),
                ("processed", models.BooleanField(default=False)),
                ("processing_error", models.TextField(blank=True, default="")),
                ("received_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-received_at"]},
        ),
        migrations.CreateModel(
            name="PaymentTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(default="paystack", max_length=30)),
                ("reference", models.CharField(max_length=120, unique=True)),
                ("purpose", models.CharField(choices=[("wallet_top_up", "Wallet top-up"), ("encounter_payment", "Encounter payment")], max_length=30)),
                ("status", models.CharField(choices=[("created", "Created"), ("initialized", "Initialized"), ("verified", "Verified"), ("posted", "Posted to finance"), ("failed", "Failed"), ("exception", "Exception")], default="created", max_length=20)),
                ("email", models.EmailField(max_length=254)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("expected_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("received_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("authorization_url", models.URLField(blank=True, default="")),
                ("provider_access_code", models.CharField(blank=True, default="", max_length=120)),
                ("provider_payload", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("failure_reason", models.TextField(blank=True, default="")),
                ("initialized_at", models.DateTimeField(blank=True, null=True)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("financial_record", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="payment_transactions", to="finance.encounterfinancialrecord")),
                ("wallet", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="payment_transactions", to="finance.organizationwallet")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="paymentwebhookevent", index=models.Index(fields=["reference", "event_name"], name="pay_wh_ref_event_idx")),
        migrations.AddIndex(model_name="paymenttransaction", index=models.Index(fields=["status", "purpose"], name="payments_pa_status_ef2398_idx")),
        migrations.AddIndex(model_name="paymenttransaction", index=models.Index(fields=["created_at"], name="payments_pa_created_a98c12_idx")),
    ]
