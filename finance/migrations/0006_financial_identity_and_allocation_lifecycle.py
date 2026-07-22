from django.db import migrations, models
import django.db.models.deletion


def populate_financial_identity(apps, schema_editor):
    FinancialRecord = apps.get_model("finance", "EncounterFinancialRecord")
    AllocationRule = apps.get_model("finance", "AllocationRule")

    for record in FinancialRecord.objects.select_related("encounter").iterator():
        encounter = record.encounter
        record.service_pathway = (
            "clinic_direct" if encounter.source_type == "clinic_direct" else "hospital_referred"
        )
        responsibility = (encounter.payment_responsibility or "").strip()
        if responsibility == "patient":
            record.payer_type = "patient"
            record.collector_type = "sentinel"
            record.payment_method = "paystack"
        elif responsibility in {"hospital", "clinic"}:
            record.payer_type = "organization"
            record.collector_type = "none"
            record.payment_method = "wallet"
        elif responsibility == "programme":
            record.payer_type = "programme"
            record.collector_type = "programme"
            record.payment_method = "unset"
        else:
            record.payer_type = "waived"
            record.collector_type = "none"
            record.payment_method = "waived"
        record.save(
            update_fields=["service_pathway", "payer_type", "collector_type", "payment_method"]
        )

    AllocationRule.objects.filter(
        beneficiary_role="hospital", beneficiary_organization__isnull=True
    ).update(beneficiary_source="referring_hospital")
    AllocationRule.objects.filter(
        beneficiary_role="clinic", beneficiary_organization__isnull=True
    ).update(beneficiary_source="testing_clinic")


class Migration(migrations.Migration):
    dependencies = [("finance", "0005_encounterfinancialrecord_payer_organization")]

    operations = [
        migrations.AddField(
            model_name="allocationrule",
            name="beneficiary_source",
            field=models.CharField(
                choices=[
                    ("fixed", "Fixed organisation"),
                    ("referring_hospital", "Encounter referring hospital"),
                    ("testing_clinic", "Encounter testing clinic"),
                ],
                default="fixed",
                help_text="How the beneficiary is resolved when an encounter is priced.",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="encounterallocation",
            name="beneficiary_source",
            field=models.CharField(
                choices=[
                    ("fixed", "Fixed organisation"),
                    ("referring_hospital", "Encounter referring hospital"),
                    ("testing_clinic", "Encounter testing clinic"),
                ],
                default="fixed",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="encounterallocation",
            name="earned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="encounterallocation",
            name="reversed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="encounterallocation",
            name="settled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="encounterallocation",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending_service", "Pending service"),
                    ("earned", "Earned"),
                    ("settlement_pending", "Settlement pending"),
                    ("settled", "Settled"),
                    ("reversed", "Reversed"),
                ],
                default="pending_service",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="encounterfinancialrecord",
            name="collecting_organization",
            field=models.ForeignKey(
                blank=True,
                help_text="Partner organisation that collected the patient's money, if any.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="collected_financial_records",
                to="organizations.organization",
            ),
        ),
        migrations.AddField(
            model_name="encounterfinancialrecord",
            name="collector_type",
            field=models.CharField(
                choices=[
                    ("sentinel", "Sentinel"), ("hospital", "Hospital"),
                    ("clinic", "Clinic"), ("programme", "Programme sponsor"),
                    ("none", "No collector"),
                ],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="encounterfinancialrecord",
            name="payer_type",
            field=models.CharField(
                choices=[
                    ("patient", "Patient"), ("organization", "Hospital or clinic"),
                    ("programme", "Programme sponsor"), ("waived", "Waived"),
                ],
                default="organization",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="encounterfinancialrecord",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("unset", "Not selected"), ("paystack", "Paystack"),
                    ("wallet", "Prefunded wallet"),
                    ("bank_transfer", "Approved bank transfer"),
                    ("pos", "Sentinel POS"),
                    ("authorized_credit", "Authorised credit"),
                    ("waived", "Waived"),
                ],
                default="unset",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="encounterfinancialrecord",
            name="service_pathway",
            field=models.CharField(
                choices=[
                    ("hospital_referred", "Hospital referred"),
                    ("clinic_direct", "Clinic direct"),
                ],
                default="hospital_referred",
                max_length=30,
            ),
        ),
        migrations.RunPython(populate_financial_identity, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="encounterallocation",
            index=models.Index(
                fields=["beneficiary_organization", "status"], name="fin_alloc_benef_status_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="encounterallocation",
            index=models.Index(fields=["status", "created_at"], name="fin_alloc_status_created_idx"),
        ),
    ]
