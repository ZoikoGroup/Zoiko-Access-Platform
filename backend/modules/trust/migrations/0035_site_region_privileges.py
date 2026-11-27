from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("trust", "0034_qualifications")]

    operations = [
        migrations.AddField(
            model_name="organization", name="region",
            field=models.CharField(blank=True, default="", max_length=60)),
        migrations.AddField(
            model_name="organization", name="privileges_enforced",
            field=models.BooleanField(default=False)),
    ]
