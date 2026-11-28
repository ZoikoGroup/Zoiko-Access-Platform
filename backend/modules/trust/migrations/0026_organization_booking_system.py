# A clinic may choose a partner connector as its booking system (modules/bookingflow). Adds one
# column with an empty default: every existing clinic keeps confirming bookings by hand.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trust', '0025_suspension_continuity'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='booking_system',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
    ]
