import finance.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0006_financial_identity_and_allocation_lifecycle"),
    ]

    operations = [
        migrations.CreateModel(
            name="BankTransferFundingRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("request_reference", models.CharField(default=finance.models.bank_transfer_request_reference, editable=False, max_length=32, unique=True)),
                ("requested_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("received_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("status", models.CharField(choices=[("awaiting_transfer", "Awaiting transfer"), ("proof_submitted", "Proof submitted"), ("under_verification", "Under verification"), ("verified", "Verified"), ("credited", "Credited"), ("underpaid", "Underpaid"), ("overpaid", "Overpaid"), ("rejected", "Rejected"), ("expired", "Expired"), ("reversed", "Reversed")], default="awaiting_transfer", max_length=30)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("proof", models.FileField(blank=True, null=True, upload_to=finance.models.bank_transfer_proof_path)),
                ("proof_submitted_at", models.DateTimeField(blank=True, null=True)),
                ("bank_transaction_reference", models.CharField(blank=True, default="", max_length=120)),
                ("value_date", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("rejection_reason", models.TextField(blank=True, default="")),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_bank_transfer_funding", to=settings.AUTH_USER_MODEL)),
                ("ledger_entry", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="bank_transfer_funding_request", to="finance.walletledgerentry")),
                ("requester", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="requested_bank_transfer_funding", to=settings.AUTH_USER_MODEL)),
                ("verified_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="verified_bank_transfer_funding", to=settings.AUTH_USER_MODEL)),
                ("wallet", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="bank_transfer_funding_requests", to="finance.organizationwallet")),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="banktransferfundingrequest",
            index=models.Index(fields=["wallet", "status"], name="fin_bank_wallet_status_idx"),
        ),
        migrations.AddIndex(
            model_name="banktransferfundingrequest",
            index=models.Index(fields=["status", "created_at"], name="fin_bank_status_created_idx"),
        ),
        migrations.AddConstraint(
            model_name="banktransferfundingrequest",
            constraint=models.UniqueConstraint(condition=~models.Q(bank_transaction_reference=""), fields=("bank_transaction_reference",), name="fin_unique_bank_transaction_ref"),
        ),
    ]
