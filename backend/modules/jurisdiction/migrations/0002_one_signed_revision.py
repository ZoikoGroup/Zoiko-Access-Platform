from django.db import migrations, models


def retire_older_signed(apps, schema_editor):
    """If an older database ever holds two signed revisions of one pack, keep the newest signed and
    retire the rest, so the new rule can be added."""
    Pack = apps.get_model("jurisdiction", "CountryPack")
    seen = set()
    for row in Pack.objects.filter(status="signed").order_by("tenant_id", "pack_ref", "-revision"):
        key = (row.tenant_id, row.pack_ref)
        if key in seen:
            row.status, row.note = "retired", "Superseded by a newer signed revision"
            row.save(update_fields=["status", "note"])
        seen.add(key)


class Migration(migrations.Migration):

    dependencies = [
        ("jurisdiction", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(retire_older_signed, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="countrypack",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "signed")), fields=("tenant_id", "pack_ref"),
                name="jurisdiction_one_signed_revision"),
        ),
    ]
