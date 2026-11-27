from django.apps import AppConfig


class SafetyConfig(AppConfig):
    """D08: clinical safety. The hazard register (docs schema file 05, `safety.hazard`)."""

    name = "modules.safety"
    label = "safety"
