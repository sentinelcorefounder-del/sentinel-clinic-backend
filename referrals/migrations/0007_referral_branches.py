from django.db import migrations, models
import django.db.models.deletion


def backfill_branches(apps, schema_editor):
    Referral = apps.get_model("referrals", "HospitalReferral")
    Branch = apps.get_model("organizations", "OrganizationBranch")
    for referral in Referral.objects.all().iterator():
        updates = []
        if referral.source_hospital_id and not referral.source_branch_id:
            branch = Branch.objects.filter(organization_id=referral.source_hospital_id, is_head_office=True).first()
            if branch:
                referral.source_branch_id = branch.id
                updates.append("source_branch")
        if referral.matched_clinic_id and not referral.matched_branch_id:
            branch = Branch.objects.filter(organization_id=referral.matched_clinic_id, is_head_office=True).first()
            if branch:
                referral.matched_branch_id = branch.id
                updates.append("matched_branch")
        if updates:
            referral.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0006_organizationbranch"),
        ("referrals", "0006_alter_hospitalreferral_referral_status"),
    ]
    operations = [
        migrations.AddField(model_name="hospitalreferral", name="source_branch", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="submitted_hospital_referrals", to="organizations.organizationbranch")),
        migrations.AddField(model_name="hospitalreferral", name="matched_branch", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="matched_hospital_referrals", to="organizations.organizationbranch")),
        migrations.RunPython(backfill_branches, migrations.RunPython.noop),
    ]
