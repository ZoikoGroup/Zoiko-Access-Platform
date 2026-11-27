from django.apps import AppConfig


class RetentionConfig(AppConfig):
    """Retention rules per customer and the report of what they would make due (DEV-213).
    Nothing in this module deletes anything."""

    name = "modules.retention"
    label = "retention"
