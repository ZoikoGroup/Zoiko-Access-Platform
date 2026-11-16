from django.apps import AppConfig


class RightsConfig(AppConfig):
    """D14: privacy and data rights. A patient's formal requests about their own data."""

    name = "modules.rights"
    label = "rights"

    def ready(self) -> None:
        # `manage.py rotate_field_keys` moves the sealed request notes onto the current key.
        from foundation import rotation

        from .rotation import rotate

        rotation.register("rights", rotate)
