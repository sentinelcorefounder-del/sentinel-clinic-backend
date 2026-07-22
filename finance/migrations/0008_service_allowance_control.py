import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0007_banktransferfundingrequest"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ServiceAllowance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("monetary_limit", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("patient_limit", models.PositiveIntegerField(blank=True, null=True)),
                ("valid_from", models.DateField()),
                ("expires_at", models.DateTimeField()),
                ("status", models.CharField(choices=[("draft", "Draft"), ("active", "Active"), ("suspended", "Suspended"), ("exhausted", "Exhausted"), ("expired", "Expired"), ("revoked", "Revoked")], default="draft", max_length=20)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_service_allowances", to=settings.AUTH_USER_MODEL)),
                ("contract", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="service_allowances", to="finance.partnercontract")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="service_allowances", to="organizations.organization")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ServiceAllowanceReservation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("status", models.CharField(choices=[("active", "Active"), ("funded", "Replaced by genuine funding"), ("released", "Released")], default="active", max_length=20)),
                ("reserved_at", models.DateTimeField(auto_now_add=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="service_allowance_reservations", to=settings.AUTH_USER_MODEL)),
                ("allowance", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reservations", to="finance.serviceallowance")),
                ("financial_record", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="allowance_reservation", to="finance.encounterfinancialrecord")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="serviceallowance", index=models.Index(fields=["organization", "status"], name="fin_allow_org_status_idx")),
        migrations.AddIndex(model_name="serviceallowance", index=models.Index(fields=["status", "expires_at"], name="fin_allow_status_exp_idx")),
        migrations.AddIndex(model_name="serviceallowancereservation", index=models.Index(fields=["allowance", "status"], name="fin_allow_res_status_idx")),
    ]
