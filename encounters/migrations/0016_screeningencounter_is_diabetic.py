from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("encounters", "0015_screeningencounter_assessment_location_snapshot_and_more")]
    operations = [
        migrations.AddField(
            model_name="screeningencounter",
            name="is_diabetic",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
