from django.db import migrations, models


class Migration(migrations.Migration):
    """Every history row now says how its digest was made.

    Rows that exist today were written with the first layout, so they get version 1. Adding a column
    with a default does not rewrite or update any row, so the table's "append-only" trigger is not
    touched. New rows carry version 2 (the shared chain helper) and are given it by the code, so the
    model keeps no default and a forgotten version fails loudly instead of being guessed."""

    dependencies = [
        ("scheduling", "0008_bookingevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookingevent",
            name="digest_version",
            field=models.PositiveSmallIntegerField(default=1),
            preserve_default=False,
        ),
    ]
