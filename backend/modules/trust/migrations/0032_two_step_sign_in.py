# Two-step sign-in with an authenticator app (DEV-380). Added columns only: the tables already have
# row-level security (Credential) or deliberately have none (Tenant, read before a tenant is bound).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trust', '0031_business_hours'),
    ]

    operations = [
        migrations.AddField(
            model_name='credential',
            name='totp_secret_enc',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='credential',
            name='totp_pending_enc',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='credential',
            name='totp_enabled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='credential',
            name='totp_last_step',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='credential',
            name='recovery_code_hashes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='tenant',
            name='staff_two_step_required',
            field=models.BooleanField(default=False),
        ),
    ]
