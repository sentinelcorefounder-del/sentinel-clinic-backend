from django.db import migrations, models
import uploads.models


class Migration(migrations.Migration):
    dependencies = [("uploads", "0011_pendingmobileimage_permanent_object_key_and_more")]
    operations = [
        migrations.AlterField(
            model_name="imageupload", name="image_upload_id",
            field=models.CharField(default=uploads.models.generate_image_upload_id, editable=False, max_length=30, unique=True),
        ),
    ]
