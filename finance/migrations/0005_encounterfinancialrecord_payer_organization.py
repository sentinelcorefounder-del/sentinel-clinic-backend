from django.db import migrations, models
import django.db.models.deletion


def populate_payer_organizations(apps, schema_editor):
    Record = apps.get_model("finance", "EncounterFinancialRecord")
    for record in Record.objects.select_related(
        "encounter__originating_organization",
        "encounter__hospital_referral__source_hospital",
    ).iterator():
        encounter = record.encounter
        payer_id = None
        if encounter.payment_responsibility == "hospital" and encounter.hospital_referral_id:
            payer_id = encounter.hospital_referral.source_hospital_id
        elif encounter.payment_responsibility in {"clinic", "programme"}:
            payer_id = encounter.originating_organization_id
        if payer_id:
            record.payer_organization_id = payer_id
            record.save(update_fields=["payer_organization"])


class Migration(migrations.Migration):
    dependencies = [("finance", "0004_settlementbatch_settlementitem")]

    operations = [
        migrations.AddField(
            model_name="encounterfinancialrecord",
            name="payer_organization",
            field=models.ForeignKey(
                blank=True,
                help_text="Organisation financially responsible for this encounter.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payer_financial_records",
                to="organizations.organization",
            ),
        ),
        migrations.RunPython(populate_payer_organizations, migrations.RunPython.noop),
    ]
