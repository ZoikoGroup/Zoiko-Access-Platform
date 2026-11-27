from django.db import migrations


class Migration(migrations.Migration):
    """Joins the partner-work kind and the workflow engine tables, added on separate branches."""

    dependencies = [
        ("workflow", "0004_merge_engine_and_data_requests"),
        ("workflow", "0004_partner_work"),
    ]

    operations = []
