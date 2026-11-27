import foundation.ids
import foundation.timeutil
from django.db import migrations, models

from foundation.rls import EnableRLS

def base() -> list[tuple[str, models.Field]]:
    return [
        ('id', models.UUIDField(default=foundation.ids.new_internal_id, editable=False, primary_key=True, serialize=False)),
        ('public_id', models.CharField(default=foundation.ids.new_public_id, editable=False, max_length=22, unique=True)),
        ('cell_id', models.CharField(editable=False, max_length=32)),
        ('tenant_id', models.UUIDField(editable=False)),
        ('entity_version', models.PositiveIntegerField(default=1)),
        ('created_at', models.DateTimeField(default=foundation.timeutil.utc_now, editable=False)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ]


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Participation',
            fields=base() + [
                ('customer_ref', models.CharField(max_length=22)),
                ('customer_name', models.CharField(max_length=120)),
                ('active', models.BooleanField(default=False)),
                ('changed_by_ref', models.CharField(max_length=22)),
            ],
            options={
                'abstract': False,
                'indexes': [models.Index(fields=['cell_id', 'tenant_id'], name='crossjoin_scope_idx')],
                'constraints': [models.UniqueConstraint(fields=('tenant_id', 'customer_ref'), name='crossjoin_one_per_customer')],
            },
        ),
        migrations.CreateModel(
            name='SharedKey',
            fields=base() + [
                ('customer_ref', models.CharField(max_length=22)),
                ('person_ref', models.CharField(max_length=22)),
                ('kind', models.CharField(max_length=16)),
                ('match_key', models.CharField(max_length=64)),
            ],
            options={
                'abstract': False,
                'indexes': [models.Index(fields=['cell_id', 'tenant_id'], name='crosskey_scope_idx'), models.Index(fields=['kind', 'match_key'], name='crosskey_lookup_idx'), models.Index(fields=['customer_ref', 'person_ref'], name='crosskey_person_idx')],
            },
        ),
        migrations.CreateModel(
            name='CrossCase',
            fields=base() + [
                ('customer_a_ref', models.CharField(max_length=22)),
                ('person_a_ref', models.CharField(max_length=22)),
                ('customer_b_ref', models.CharField(max_length=22)),
                ('person_b_ref', models.CharField(max_length=22)),
                ('factors', models.JSONField(default=list)),
                ('state', models.CharField(choices=[('open', 'Waiting for both organizations'), ('confirmed', 'Both say it is the same person'), ('not_same', 'Not the same person')], default='open', max_length=12)),
                ('a_answer', models.CharField(blank=True, default='', max_length=12)),
                ('b_answer', models.CharField(blank=True, default='', max_length=12)),
                ('a_answered_by_ref', models.CharField(blank=True, default='', max_length=22)),
                ('b_answered_by_ref', models.CharField(blank=True, default='', max_length=22)),
            ],
            options={
                'abstract': False,
                'indexes': [models.Index(fields=['cell_id', 'tenant_id'], name='crosscase_scope_idx')],
                'constraints': [models.UniqueConstraint(fields=('tenant_id', 'customer_a_ref', 'person_a_ref', 'customer_b_ref', 'person_b_ref'), name='crosscase_one_per_pair')],
            },
        ),
        EnableRLS("crossmatch_participation"),
        EnableRLS("crossmatch_sharedkey"),
        EnableRLS("crossmatch_crosscase"),
    ]
