from django.db import migrations, models
import django.db.models.deletion


def backfill_service_branch(apps, schema_editor):
    Encounter = apps.get_model("encounters", "ScreeningEncounter")
    Branch = apps.get_model("organizations", "OrganizationBranch")
    for encounter in Encounter.objects.filter(service_branch__isnull=True).iterator():
        organization_id = encounter.patient.assigned_clinic_id
        if organization_id:
            branch = Branch.objects.filter(organization_id=organization_id, is_head_office=True).first()
            if branch:
                encounter.service_branch_id = branch.id
                encounter.save(update_fields=["service_branch"])


class Migration(migrations.Migration):
    dependencies = [
        ("encounters", "0011_ocular_report_composition"),
        ("organizations", "0006_organizationbranch"),
        ("patients", "0004_branch_and_safe_sequence"),
    ]
    operations = [
        migrations.AddField(
            model_name="screeningencounter",
            name="service_branch",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="screening_encounters", to="organizations.organizationbranch"),
        ),
        migrations.RunPython(backfill_service_branch, migrations.RunPython.noop),
    ]
