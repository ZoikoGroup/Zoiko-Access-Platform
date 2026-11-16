from django.db import migrations


class Migration(migrations.Migration):
    """Joins the workflow engine tables and the data-request work kind, which were added separately
    after 0002. They touch different tables."""

    dependencies = [
        ('workflow', '0003_workflow_engine'),
        ('workflow', '0003_data_request_kind'),
    ]

    operations = []
