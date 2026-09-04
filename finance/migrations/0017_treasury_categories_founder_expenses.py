import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import finance.models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0016_encountersponsorship_sponsorshipevent_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FounderFundedExpense",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("expense_reference", models.CharField(default=finance.models.founder_expense_reference, editable=False, max_length=32, unique=True)),
                ("expense_date", models.DateField()),
                ("category", models.CharField(choices=[("salary_payroll", "Salary / payroll"), ("contractor", "Contractor"), ("hosting_software", "Hosting / software"), ("field_operations", "Field operations"), ("marketing_administration", "Marketing / administration"), ("equipment_supplies", "Equipment / supplies"), ("tax_professional_fees", "Tax / professional fees"), ("founder_reimbursement", "Founder reimbursement"), ("internal_account_transfer", "Internal account transfer"), ("other_operating_expense", "Other approved operating expense")], max_length=40)),
                ("supplier_payee", models.CharField(max_length=180)),
                ("description", models.TextField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("evidence", models.FileField(upload_to=finance.models.founder_expense_evidence_path)),
                ("funding_treatment", models.CharField(choices=[("founder_contribution", "Founder contribution — not repayable"), ("founder_reimbursable", "Amount owed to founder — reimbursable")], max_length=30)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved"), ("rejected", "Rejected"), ("settled", "Settled"), ("cancelled", "Cancelled")], default="draft", max_length=20)),
                ("idempotency_key", models.CharField(max_length=120, unique=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_reason", models.TextField(blank=True, default="")),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_founder_expenses", to=settings.AUTH_USER_MODEL)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="decided_founder_expenses", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-expense_date", "-id"]},
        ),
        migrations.CreateModel(
            name="FounderFundedExpenseEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=60)),
                ("source_status", models.CharField(blank=True, default="", max_length=20)),
                ("target_status", models.CharField(max_length=20)),
                ("reason", models.TextField(blank=True, default="")),
                ("idempotency_key", models.CharField(max_length=160, unique=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="founder_expense_events", to=settings.AUTH_USER_MODEL)),
                ("expense", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="finance.founderfundedexpense")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddField(
            model_name="treasurytransfer",
            name="category",
            field=models.CharField(choices=[("salary_payroll", "Salary / payroll"), ("contractor", "Contractor"), ("hosting_software", "Hosting / software"), ("field_operations", "Field operations"), ("marketing_administration", "Marketing / administration"), ("equipment_supplies", "Equipment / supplies"), ("tax_professional_fees", "Tax / professional fees"), ("founder_reimbursement", "Founder reimbursement"), ("internal_account_transfer", "Internal account transfer"), ("other_operating_expense", "Other approved operating expense")], default="other_operating_expense", max_length=40),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="treasurytransfer",
            name="execution_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="treasurytransfer",
            name="founder_expense",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reimbursement_transfers", to="finance.founderfundedexpense"),
        ),
        migrations.AddIndex(
            model_name="founderfundedexpense",
            index=models.Index(fields=["status", "expense_date"], name="fin_founder_status_date_idx"),
        ),
        migrations.AddConstraint(
            model_name="founderfundedexpense",
            constraint=models.CheckConstraint(condition=models.Q(amount__gt=0), name="fin_founder_expense_positive"),
        ),
    ]
