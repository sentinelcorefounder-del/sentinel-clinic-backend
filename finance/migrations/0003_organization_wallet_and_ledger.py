from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0002_rename_finance_enc_status_093a6f_idx_finance_enc_status_927306_idx_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationWallet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("is_active", models.BooleanField(default=True)),
                ("credit_limit", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("notes", models.TextField(blank=True, default="")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="finance_wallets", to="organizations.organization")),
            ],
            options={"ordering": ["organization__name", "currency"]},
        ),
        migrations.CreateModel(
            name="WalletReservation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("captured_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("released_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("status", models.CharField(choices=[("active", "Active"), ("partially_captured", "Partially captured"), ("captured", "Captured"), ("partially_released", "Partially released"), ("released", "Released"), ("cancelled", "Cancelled")], default="active", max_length=30)),
                ("idempotency_key", models.CharField(max_length=120, unique=True)),
                ("reference", models.CharField(blank=True, default="", max_length=120)),
                ("reserved_at", models.DateTimeField(auto_now_add=True)),
                ("captured_at", models.DateTimeField(blank=True, null=True)),
                ("released_at", models.DateTimeField(blank=True, null=True)),
                ("financial_record", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="wallet_reservations", to="finance.encounterfinancialrecord")),
                ("wallet", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reservations", to="finance.organizationwallet")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="WalletLedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entry_type", models.CharField(choices=[("top_up", "Top up"), ("service_reservation", "Service reservation"), ("service_capture", "Service capture"), ("reservation_release", "Reservation release"), ("refund", "Refund"), ("reversal", "Reversal"), ("adjustment", "Adjustment"), ("settlement", "Settlement"), ("transfer", "Transfer"), ("write_off", "Write off")], max_length=40)),
                ("available_delta", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("reserved_delta", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("idempotency_key", models.CharField(max_length=120, unique=True)),
                ("reference", models.CharField(blank=True, default="", max_length=120)),
                ("description", models.CharField(blank=True, default="", max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="wallet_ledger_entries", to=settings.AUTH_USER_MODEL)),
                ("financial_record", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="wallet_ledger_entries", to="finance.encounterfinancialrecord")),
                ("related_entry", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="follow_up_entries", to="finance.walletledgerentry")),
                ("reservation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="finance.walletreservation")),
                ("wallet", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="finance.organizationwallet")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="organizationwallet",
            constraint=models.UniqueConstraint(fields=("organization", "currency"), name="finance_unique_org_wallet_currency"),
        ),
        migrations.AddIndex(model_name="walletreservation", index=models.Index(fields=["wallet", "status"], name="finance_wal_wallet__1c9971_idx")),
        migrations.AddIndex(model_name="walletreservation", index=models.Index(fields=["financial_record", "status"], name="finance_wal_financi_f5d88f_idx")),
        migrations.AddIndex(model_name="walletledgerentry", index=models.Index(fields=["wallet", "created_at"], name="finance_wal_wallet__c065f5_idx")),
        migrations.AddIndex(model_name="walletledgerentry", index=models.Index(fields=["entry_type", "created_at"], name="finance_wal_entry_t_04ed32_idx")),
        migrations.AddIndex(model_name="walletledgerentry", index=models.Index(fields=["financial_record", "created_at"], name="finance_wal_financi_49d1f9_idx")),
    ]
