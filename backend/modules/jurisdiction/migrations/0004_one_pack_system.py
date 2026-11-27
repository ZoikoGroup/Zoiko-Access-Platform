"""One country-pack system (DEV-364): the cell-wide packs of the retired `packs` module are gone.

Their tables held only configuration (no patient data) and nothing reads them any more. The
module's migration records are removed too, so a later module of that name would start clean.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("jurisdiction", "0003_pack_signature")]

    operations = [
        migrations.RunSQL(
            "DROP TABLE IF EXISTS packs_countrypack; DROP TABLE IF EXISTS packs_revocationstate; "
            "DELETE FROM django_migrations WHERE app = 'packs';",
            migrations.RunSQL.noop),
    ]
