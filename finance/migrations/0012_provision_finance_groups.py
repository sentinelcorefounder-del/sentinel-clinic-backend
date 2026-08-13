from django.db import migrations


FINANCE_GROUPS = (
    "finance_viewer",
    "finance_operator",
    "finance_approver",
    "finance_admin",
    "finance_tester",
)


def provision_finance_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in FINANCE_GROUPS:
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0011_billing_profile_and_funding_documents"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(provision_finance_groups, migrations.RunPython.noop),
    ]
