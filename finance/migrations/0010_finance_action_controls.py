from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import finance.models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0009_versioned_pricing_and_settlement_controls"),
    ]

    operations = [
        migrations.AddField(
            model_name="settlementbatch",
            name="prepared_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="prepared_finance_settlements", to=settings.AUTH_USER_MODEL),
        ),
        migrations.CreateModel(
            name="FinanceActionRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("action_type", models.CharField(choices=[("refund", "Refund"), ("reversal", "Reversal"), ("adjustment", "Adjustment")], max_length=20)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("reason", models.TextField()),
                ("external_reference", models.CharField(max_length=120)),
                ("evidence", models.FileField(blank=True, null=True, upload_to=finance.models.finance_action_evidence_path)),
                ("idempotency_key", models.CharField(max_length=120, unique=True)),
                ("status", models.CharField(choices=[("pending", "Pending approval"), ("approved", "Approved and posted"), ("rejected", "Rejected"), ("cancelled", "Cancelled")], default="pending", max_length=20)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_reason", models.TextField(blank=True, default="")),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="decided_finance_actions", to=settings.AUTH_USER_MODEL)),
                ("financial_record", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="finance_action_requests", to="finance.encounterfinancialrecord")),
                ("posted_entry", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approved_finance_action", to="finance.walletledgerentry")),
                ("related_entry", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="finance_action_requests", to="finance.walletledgerentry")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="requested_finance_actions", to=settings.AUTH_USER_MODEL)),
                ("wallet", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="finance_action_requests", to="finance.organizationwallet")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="FinanceControlAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=80)),
                ("before_state", models.JSONField(blank=True, default=dict)),
                ("after_state", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("action_request", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="audit_entries", to="finance.financeactionrequest")),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="finance_control_audits", to=settings.AUTH_USER_MODEL)),
                ("financial_record", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="control_audits", to="finance.encounterfinancialrecord")),
                ("settlement_batch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="control_audits", to="finance.settlementbatch")),
                ("wallet", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="control_audits", to="finance.organizationwallet")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(model_name="financeactionrequest", index=models.Index(fields=["status", "action_type"], name="fin_action_status_type_idx")),
        migrations.AddIndex(model_name="financeactionrequest", index=models.Index(fields=["wallet", "status"], name="fin_action_wallet_status_idx")),
    ]
