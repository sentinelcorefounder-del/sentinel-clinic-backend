from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0017_alter_eyehealthscreeningreport_review_status_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="eyehealthscreeningreport",
            name="selected_ocular_investigation_ids",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
