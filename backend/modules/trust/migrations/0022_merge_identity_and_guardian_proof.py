from django.db import migrations


class Migration(migrations.Migration):
    """Joins two lines of work that each added a migration after 0019: the clinic booking system and
    identity matching, and the customer country and guardian proof. They touch different tables, so
    nothing else is needed."""

    dependencies = [
        ('trust', '0021_identity_matching'),
        ('trust', '0021_guardian_proof'),
    ]

    operations = []
