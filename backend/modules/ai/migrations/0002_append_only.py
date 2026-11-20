from django.db import migrations

TABLES = ["ai_aipermitevent", "ai_airequest", "ai_airesponseevidence"]

# The database itself refuses to change or remove AI history, even for the table owner.
# (TRUNCATE is not trapped: Django's test cleanup needs it. The app role is never granted it.)
FORWARD = """
CREATE FUNCTION ai_history_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'AI history is append-only' USING ERRCODE = '42501';
END $$;
""" + "".join(f"""
CREATE TRIGGER {t}_no_change BEFORE UPDATE OR DELETE ON {t}
    FOR EACH ROW EXECUTE FUNCTION ai_history_append_only();
""" for t in TABLES)

BACKWARD = "".join(f"DROP TRIGGER {t}_no_change ON {t};\n" for t in TABLES) + \
    "DROP FUNCTION ai_history_append_only();\n"


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0001_initial"),
    ]

    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
