import foundation.ids
import foundation.timeutil
from django.db import migrations, models

from foundation.rls import EnableRLS

# What was sent, to whom and when can never be changed or removed: not even by the table owner.
APPEND_ONLY_SQL = """
CREATE FUNCTION evidence_disclosure_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the disclosure log is append-only' USING ERRCODE = '42501';
END $$;
CREATE TRIGGER evidence_disclosure_no_change BEFORE UPDATE OR DELETE ON evidence_disclosure
    FOR EACH ROW EXECUTE FUNCTION evidence_disclosure_append_only();
"""
APPEND_ONLY_UNDO = """
DROP TRIGGER evidence_disclosure_no_change ON evidence_disclosure;
DROP FUNCTION evidence_disclosure_append_only();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('evidence', '0002_audit_who_and_why'),
    ]

    operations = [
        migrations.CreateModel(
            name='Disclosure',
            fields=[
                ('id', models.UUIDField(default=foundation.ids.new_internal_id, editable=False, primary_key=True, serialize=False)),
                ('public_id', models.CharField(default=foundation.ids.new_public_id, editable=False, max_length=22, unique=True)),
                ('cell_id', models.CharField(editable=False, max_length=32)),
                ('tenant_id', models.UUIDField(editable=False)),
                ('entity_version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(default=foundation.timeutil.utc_now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('person_ref', models.CharField(max_length=22)),
                ('kind', models.CharField(choices=[('referral', 'A referral'), ('request', 'A request to a partner'), ('claim', 'A claim to a payer')], max_length=12)),
                ('stage', models.CharField(choices=[('sent', 'Sent'), ('released', 'Details released with the patient’s agreement'), ('withdrawn', 'Agreement taken back')], max_length=12)),
                ('record_ref', models.CharField(max_length=22)),
                ('organization_ref', models.CharField(blank=True, default='', max_length=22)),
                ('recipient_ref', models.CharField(blank=True, default='', max_length=22)),
                ('recipient_name', models.CharField(blank=True, default='', max_length=200)),
                ('actor_ref', models.CharField(max_length=64)),
            ],
            options={
                'abstract': False,
                'indexes': [models.Index(fields=['cell_id', 'tenant_id'], name='disclosure_scope_idx'), models.Index(fields=['person_ref', 'created_at'], name='disclosure_person_idx'), models.Index(fields=['record_ref', 'created_at'], name='disclosure_record_idx')],
            },
        ),
        EnableRLS("evidence_disclosure"),
        migrations.RunSQL(APPEND_ONLY_SQL, APPEND_ONLY_UNDO),
    ]
