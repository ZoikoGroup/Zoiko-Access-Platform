from django.apps import AppConfig


class CoverageConfig(AppConfig):
    """D12: Payer, entitlements and coverage: a patient's insurance policies."""

    name = "modules.coverage"
    label = "coverage"

    def ready(self) -> None:
        # `manage.py rotate_field_keys` moves this module's encrypted values onto the current key.
        from foundation import rotation

        from .rotation import rotate

        rotation.register("coverage", rotate)
