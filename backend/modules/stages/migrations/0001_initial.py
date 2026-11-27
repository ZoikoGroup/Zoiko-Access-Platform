import foundation.ids
import foundation.timeutil
from django.db import migrations, models

from foundation.rls import EnableRLS

# A stage, once written down, is never changed or removed: not even by the table owner.
APPEND_ONLY_SQL = """
CREATE FUNCTION stages_stagestep_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'stage steps are append-only' USING ERRCODE = '42501';
END $$;
CREATE TRIGGER stages_stagestep_no_change BEFORE UPDATE OR DELETE ON stages_stagestep
    FOR EACH ROW EXECUTE FUNCTION stages_stagestep_append_only();
"""
APPEND_ONLY_UNDO = """
DROP TRIGGER stages_stagestep_no_change ON stages_stagestep;
DROP FUNCTION stages_stagestep_append_only();
"""


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='StageStep',
            fields=[
                ('id', models.UUIDField(default=foundation.ids.new_internal_id, editable=False, primary_key=True, serialize=False)),
                ('public_id', models.CharField(default=foundation.ids.new_public_id, editable=False, max_length=22, unique=True)),
                ('cell_id', models.CharField(editable=False, max_length=32)),
                ('tenant_id', models.UUIDField(editable=False)),
                ('entity_version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(default=foundation.timeutil.utc_now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('subject_type', models.CharField(choices=[('booking', 'Booking'), ('referral', 'Referral')], max_length=12)),
                ('subject_ref', models.CharField(max_length=22)),
                ('seq', models.PositiveIntegerField()),
                ('state', models.CharField(max_length=32)),
                ('seen_at', models.DateTimeField()),
                ('cause', models.CharField(max_length=64)),
            ],
            options={
                'abstract': False,
                'indexes': [models.Index(fields=['cell_id', 'tenant_id'], name='stagestep_scope_idx'), models.Index(fields=['subject_type', 'subject_ref', 'seq'], name='stagestep_subject_idx')],
                'constraints': [models.UniqueConstraint(fields=('tenant_id', 'subject_type', 'subject_ref', 'seq'), name='stagestep_one_per_seq')],
            },
        ),
        EnableRLS("stages_stagestep"),
        migrations.RunSQL(APPEND_ONLY_SQL, APPEND_ONLY_UNDO),
    ]
