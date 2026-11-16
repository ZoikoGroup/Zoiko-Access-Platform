from django.db import migrations, models


class Migration(migrations.Migration):
    """T-43: a new partner starts PENDING and has no secret until Zoiko's three checks are
    recorded. Rows that already exist keep the state they have."""

    dependencies = [
        ("integration", "0003_delivery_evidence"),
    ]

    operations = [
        migrations.AddField(
            model_name="partnerclient",
            name="validation",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="partnerclient",
            name="state",
            field=models.CharField(
                choices=[("pending", "Waiting for Zoiko's checks"), ("active", "Active"),
                         ("suspended", "Suspended"), ("revoked", "Revoked")],
                default="pending", max_length=12),
        ),
    ]
