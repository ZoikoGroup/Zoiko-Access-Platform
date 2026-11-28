from django.db import migrations, models


class Migration(migrations.Migration):
    """DEV-324: the author may keep a note from a guardian; erased text after retention."""

    dependencies = [
        ("clinicalnote", "0002_note_addendum"),
    ]

    operations = [
        migrations.AddField(
            model_name="clinicalnote",
            name="guardian_hidden",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="clinicalnote",
            name="erased_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
