from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trust', '0019_organization_response_target'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='booking_system',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
    ]
