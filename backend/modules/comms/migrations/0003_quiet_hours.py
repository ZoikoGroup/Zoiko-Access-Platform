from django.db import migrations, models


class Migration(migrations.Migration):
    """TC30: an optional quiet window per login for routine reminder emails."""

    dependencies = [
        ("comms", "0002_emailpreference"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailpreference",
            name="quiet_start",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="emailpreference",
            name="quiet_end",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="emailpreference",
            name="quiet_zone",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
