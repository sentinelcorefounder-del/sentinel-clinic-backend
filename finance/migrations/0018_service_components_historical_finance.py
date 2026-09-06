import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0017_treasury_categories_founder_expenses"),
        ("encounters", "0015_screeningencounter_assessment_location_snapshot_and_more"),
        ("organizations", "0009_organization_treasury_designation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="walletledgerentry",
            name="entry_type",
            field=models.CharField(
                choices=[
                    ("top_up", "Top up"),
                    ("service_reservation", "Service reservation"),
                    ("service_capture", "Service capture"),
                    ("reservation_release", "Reservation release"),
                    ("refund", "Refund"),
                    ("reversal", "Reversal"),
                    ("adjustment", "Adjustment"),
                    ("settlement", "Settlement"),
                    ("transfer", "Transfer"),
                    ("write_off", "Write off"),
                    ("opening_balance", "Opening balance"),
                    ("patient_receipt", "Patient receipt"),
                ],
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name="HistoricalAssessmentFinance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("service_code", models.CharField(choices=[("diabetic_retinal_assessment", "Diabetic retinal assessment"), ("combined_diabetic_eye_health", "Combined diabetic eye health assessment"), ("eye_health_screening", "Eye health assessment"), ("ocular_assessment", "Comprehensive ocular assessment"), ("ocular_ai_review", "Ocular AI clinical review")], max_length=80)),
                ("assessment_date", models.DateField()),
                ("payment_state", models.CharField(choices=[("historical_paid", "Already paid historically"), ("historical_unpaid", "Still unpaid"), ("historical_unknown", "Payment status unknown")], max_length=30)),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("amount_paid", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("payment_method", models.CharField(blank=True, default="", max_length=40)),
                ("payment_reference", models.CharField(blank=True, default="", max_length=120)),
                ("source_note", models.TextField()),
                ("idempotency_key", models.CharField(max_length=160, unique=True)),
                ("collecting_organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="historical_collections", to="organizations.organization")),
                ("encounter", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="historical_finance", to="encounters.screeningencounter")),
                ("imported_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="imported_historical_finance", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-assessment_date", "-id"]},
        ),
        migrations.CreateModel(
            name="EncounterChargeComponent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("service_code", models.CharField(choices=[("diabetic_retinal_assessment", "Diabetic retinal assessment"), ("combined_diabetic_eye_health", "Combined diabetic eye health assessment"), ("eye_health_screening", "Eye health assessment"), ("ocular_assessment", "Comprehensive ocular assessment"), ("ocular_ai_review", "Ocular AI clinical review")], max_length=80)),
                ("description", models.CharField(max_length=180)),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("unit_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("gross_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(default="NGN", max_length=3)),
                ("pricing_snapshot", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("open", "Open"), ("captured", "Captured"), ("cancelled", "Cancelled"), ("refunded", "Refunded")], default="open", max_length=20)),
                ("idempotency_key", models.CharField(max_length=160, unique=True)),
                ("financial_record", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="charge_components", to="finance.encounterfinancialrecord")),
                ("pricing_rule", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="charge_components", to="finance.pricingrule")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="encounterchargecomponent",
            constraint=models.CheckConstraint(condition=models.Q(quantity__gt=0), name="fin_charge_component_quantity_gt_zero"),
        ),
        migrations.AddConstraint(
            model_name="encounterchargecomponent",
            constraint=models.CheckConstraint(condition=models.Q(unit_amount__gte=0), name="fin_charge_component_unit_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="encounterchargecomponent",
            constraint=models.CheckConstraint(condition=models.Q(gross_amount__gte=0), name="fin_charge_component_gross_nonnegative"),
        ),
    ]
