from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trust", "0012_namecheck"),
    ]

    operations = [
        migrations.AddField(
            model_name="carerelationship",
            name="organization_ref",
            field=models.CharField(blank=True, default="", max_length=22),
        ),
    ]
