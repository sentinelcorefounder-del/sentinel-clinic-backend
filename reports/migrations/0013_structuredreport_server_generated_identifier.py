from django.db import migrations, models
import reports.models


class Migration(migrations.Migration):
    dependencies = [("reports", "0012_eyehealthscreeningreport_correction_reason_and_more")]
    operations = [
        migrations.AlterField(
            model_name="structuredreport", name="report_id",
            field=models.CharField(default=reports.models.generate_report_id, editable=False, max_length=30, unique=True),
        ),
    ]
