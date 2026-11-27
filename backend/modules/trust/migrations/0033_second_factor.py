import django.db.models.deletion
import foundation.ids
import foundation.timeutil
from django.db import migrations, models

from foundation.rls import EnableRLS


class Migration(migrations.Migration):

    dependencies = [
        ('trust', '0032_business_hours'),
    ]

    operations = [
        migrations.AddField(
            model_name='externalidentity',
            name='unlinked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='SecondFactor',
            fields=[
                ('id', models.UUIDField(default=foundation.ids.new_internal_id, editable=False, primary_key=True, serialize=False)),
                ('public_id', models.CharField(default=foundation.ids.new_public_id, editable=False, max_length=22, unique=True)),
                ('cell_id', models.CharField(editable=False, max_length=32)),
                ('tenant_id', models.UUIDField(editable=False)),
                ('entity_version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(default=foundation.timeutil.utc_now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('secret_enc', models.TextField()),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('last_used_step', models.BigIntegerField(default=0)),
                ('recovery_hashes', models.JSONField(default=list)),
                ('actor', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='second_factor', to='trust.actor')),
            ],
            options={
                'abstract': False,
                'indexes': [models.Index(fields=['cell_id', 'tenant_id'], name='secondfactor_scope_idx')],
            },
        ),
        EnableRLS("trust_secondfactor"),
    ]
