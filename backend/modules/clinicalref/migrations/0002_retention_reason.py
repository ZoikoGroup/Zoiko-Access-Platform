from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clinicalref', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='clinicalreference',
            name='withdrawn_reason',
            field=models.CharField(blank=True, choices=[('entered_in_error', 'Entered in error'), ('wrong_patient', 'Recorded for the wrong patient'), ('removed_at_source', "No longer held in the clinic's system"), ('other', 'Other'), ('retention_period', 'Erased after the retention period')], default='', max_length=24),
        ),
    ]
