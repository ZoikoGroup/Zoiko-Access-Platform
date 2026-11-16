from django.db import migrations, models


class Migration(migrations.Migration):
    """AT38: an incident can be logged with an unknown severity. It cannot be resolved until a
    safety officer assesses it, and who did so is kept."""

    dependencies = [
        ("trust", "0021_guardian_proof"),
    ]

    operations = [
        migrations.AddField(
            model_name="incident",
            name="assessed_by_ref",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="incident",
            name="assessment_note",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AlterField(
            model_name="incident",
            name="severity",
            field=models.CharField(
                choices=[
                    ("sev0", "SEV-0: patient safety, a large unlawful disclosure, or a systemic compromise"),
                    ("sev1", "SEV-1: a major regional loss or a critical workflow failure"),
                    ("sev2", "SEV-2: a partial service degradation"),
                    ("sev3", "SEV-3: a minor operational issue"),
                    ("unknown", "Not yet assessed: stays open until a safety officer evaluates it"),
                ],
                max_length=8),
        ),
    ]
