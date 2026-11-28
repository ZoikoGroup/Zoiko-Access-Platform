from django.db import migrations

TABLE = "trust_releasenote"

# The release log is add-only (DEV-077). Until now only the application role's missing rights
# enforced that; this makes the database itself refuse UPDATE and DELETE, even for the table owner,
# the same way as the audit log, the booking history and the work history.
# (TRUNCATE is not trapped: Django's test cleanup needs it. The app role is never granted it.)
APPEND_ONLY_SQL = f"""
CREATE FUNCTION trust_releasenote_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'release log entries are append-only' USING ERRCODE = '42501';
END $$;
CREATE TRIGGER trust_releasenote_no_change BEFORE UPDATE OR DELETE ON {TABLE}
    FOR EACH ROW EXECUTE FUNCTION trust_releasenote_append_only();
"""
APPEND_ONLY_UNDO = f"""
DROP TRIGGER trust_releasenote_no_change ON {TABLE};
DROP FUNCTION trust_releasenote_append_only();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("trust", "0010_suspension"),
    ]

    operations = [
        migrations.RunSQL(APPEND_ONLY_SQL, APPEND_ONLY_UNDO),
    ]
