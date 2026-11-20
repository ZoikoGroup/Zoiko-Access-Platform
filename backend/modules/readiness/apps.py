from django.apps import AppConfig


class ReadinessConfig(AppConfig):
    """GHE-20: go-live readiness for a release in a country (recorded, never executed)."""

    name = "modules.readiness"
    label = "readiness"
