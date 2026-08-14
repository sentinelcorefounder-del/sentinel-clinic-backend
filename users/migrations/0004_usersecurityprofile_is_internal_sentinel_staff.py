from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0003_userbranchaccess"),
    ]

    operations = [
        migrations.AddField(
            model_name="usersecurityprofile",
            name="is_internal_sentinel_staff",
            field=models.BooleanField(default=False),
        ),
    ]
