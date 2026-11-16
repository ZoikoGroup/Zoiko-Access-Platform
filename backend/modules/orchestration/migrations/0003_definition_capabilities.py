from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orchestration', '0002_start_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowdefinition',
            name='required_capabilities',
            field=models.JSONField(default=list),
        ),
    ]
