from django.apps import AppConfig


class TrustConfig(AppConfig):
    """D01: Trust, parties and tenancy: orgs, actors, persons, roles, delegation."""

    name = "modules.trust"
    label = "trust"

    def ready(self) -> None:
        # `manage.py rotate_field_keys` moves this module's encrypted values onto the current key.
        from foundation import rotation

        from .rotation import rotate

        rotation.register("trust", rotate)
