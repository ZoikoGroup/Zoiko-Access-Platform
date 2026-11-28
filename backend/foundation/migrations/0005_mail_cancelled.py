from django.db import migrations, models


class Migration(migrations.Migration):
    """TC37: a queued email can be cancelled before it is sent (the patient turned emails off)."""

    dependencies = [
        ("foundation", "0004_email_attempt"),
    ]

    operations = [
        migrations.AlterField(
            model_name="queuedemail",
            name="state",
            field=models.CharField(
                choices=[("queued", "Queued"), ("sending", "Sending"), ("sent", "Sent"),
                         ("failed", "Failed"), ("cancelled", "Cancelled before it was sent")],
                default="queued", max_length=16),
        ),
    ]
