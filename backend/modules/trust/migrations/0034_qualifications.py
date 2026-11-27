import django.db.models.deletion
import foundation.ids
import foundation.timeutil
from django.db import migrations, models

from foundation.rls import EnableRLS


class Migration(migrations.Migration):

    dependencies = [
        ('trust', '0033_second_factor'),
    ]

    operations = [
        migrations.CreateModel(
            name='Qualification',
            fields=[
                ('id', models.UUIDField(default=foundation.ids.new_internal_id, editable=False, primary_key=True, serialize=False)),
                ('public_id', models.CharField(default=foundation.ids.new_public_id, editable=False, max_length=22, unique=True)),
                ('cell_id', models.CharField(editable=False, max_length=32)),
                ('tenant_id', models.UUIDField(editable=False)),
                ('entity_version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(default=foundation.timeutil.utc_now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(max_length=16)),
                ('title', models.CharField(max_length=120)),
                ('issuer', models.CharField(max_length=120)),
                ('reference', models.CharField(blank=True, default='', max_length=64)),
                ('obtained_on', models.DateField(blank=True, null=True)),
                ('expires_on', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[('recorded', 'Written down, not checked'), ('verified', 'Checked'), ('withdrawn', 'Withdrawn')], default='recorded', max_length=12)),
                ('added_by_ref', models.CharField(max_length=22)),
                ('checked_by_ref', models.CharField(blank=True, default='', max_length=22)),
                ('checked_on', models.DateField(blank=True, null=True)),
                ('check_note', models.CharField(blank=True, default='', max_length=200)),
                ('withdrawn_by_ref', models.CharField(blank=True, default='', max_length=22)),
                ('withdrawn_reason', models.CharField(blank=True, default='', max_length=200)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='qualifications', to='trust.actor')),
            ],
            options={
                'abstract': False,
                'indexes': [models.Index(fields=['cell_id', 'tenant_id'], name='qualification_scope_idx')],
            },
        ),
        migrations.CreateModel(
            name='Privilege',
            fields=[
                ('id', models.UUIDField(default=foundation.ids.new_internal_id, editable=False, primary_key=True, serialize=False)),
                ('public_id', models.CharField(default=foundation.ids.new_public_id, editable=False, max_length=22, unique=True)),
                ('cell_id', models.CharField(editable=False, max_length=32)),
                ('tenant_id', models.UUIDField(editable=False)),
                ('entity_version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(default=foundation.timeutil.utc_now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization_ref', models.CharField(max_length=22)),
                ('service_ref', models.CharField(max_length=22)),
                ('valid_from', models.DateField()),
                ('valid_until', models.DateField(blank=True, null=True)),
                ('note', models.CharField(blank=True, default='', max_length=200)),
                ('granted_by_ref', models.CharField(max_length=22)),
                ('withdrawn_at', models.DateTimeField(blank=True, null=True)),
                ('withdrawn_by_ref', models.CharField(blank=True, default='', max_length=22)),
                ('withdrawn_reason', models.CharField(blank=True, default='', max_length=200)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='privileges', to='trust.actor')),
            ],
            options={
                'abstract': False,
                'indexes': [models.Index(fields=['cell_id', 'tenant_id'], name='privilege_scope_idx')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('withdrawn_at__isnull', True)), fields=('tenant_id', 'actor', 'organization_ref', 'service_ref'), name='privilege_one_live')],
            },
        ),
        EnableRLS("trust_qualification"),
        EnableRLS("trust_privilege"),
    ]
