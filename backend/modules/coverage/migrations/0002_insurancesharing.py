import foundation.ids
import foundation.timeutil
from django.db import migrations, models

from foundation.rls import EnableRLS


class Migration(migrations.Migration):

    dependencies = [
        ('coverage', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='InsuranceSharing',
            fields=[
                ('id', models.UUIDField(default=foundation.ids.new_internal_id, editable=False, primary_key=True, serialize=False)),
                ('public_id', models.CharField(default=foundation.ids.new_public_id, editable=False, max_length=22, unique=True)),
                ('cell_id', models.CharField(editable=False, max_length=32)),
                ('tenant_id', models.UUIDField(editable=False)),
                ('entity_version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(default=foundation.timeutil.utc_now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('person_ref', models.CharField(max_length=22)),
                ('share_with_staff', models.BooleanField(default=False)),
                ('changed_by_ref', models.CharField(max_length=22)),
            ],
            options={
                'abstract': False,
                'indexes': [models.Index(fields=['cell_id', 'tenant_id'], name='coveragesharing_scope_idx')],
                'constraints': [models.UniqueConstraint(fields=('cell_id', 'tenant_id', 'person_ref'), name='coveragesharing_person_uq')],
            },
        ),
        EnableRLS("coverage_insurancesharing"),
    ]
