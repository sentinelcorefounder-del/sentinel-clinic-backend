from django.db import migrations, models
import django.db.models.deletion


def initialise_identity_and_branches(apps, schema_editor):
    MasterPatient = apps.get_model("patients", "MasterPatient")
    Sequence = apps.get_model("patients", "MasterPatientSequence")
    Patient = apps.get_model("patients", "Patient")
    Branch = apps.get_model("organizations", "OrganizationBranch")

    highest = 0
    for value in MasterPatient.objects.values_list("sentinel_patient_id", flat=True):
        try:
            highest = max(highest, int(value.rsplit("-", 1)[-1]))
        except (TypeError, ValueError):
            continue
    Sequence.objects.get_or_create(pk=1, defaults={"next_value": highest + 1})

    for patient in Patient.objects.filter(
        assigned_clinic__isnull=False,
        assigned_branch__isnull=True,
    ).iterator():
        branch = Branch.objects.filter(
            organization_id=patient.assigned_clinic_id,
            is_head_office=True,
        ).first()
        if branch:
            patient.assigned_branch_id = branch.id
            patient.save(update_fields=["assigned_branch"])


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0006_organizationbranch"),
        ("patients", "0003_masterpatient_historicalrecordaccessrequest_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="patient",
            name="assigned_branch",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="patients", to="organizations.organizationbranch"),
        ),
        migrations.CreateModel(
            name="MasterPatientSequence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("next_value", models.PositiveBigIntegerField(default=1)),
            ],
        ),
        migrations.RunPython(initialise_identity_and_branches, migrations.RunPython.noop),
    ]
