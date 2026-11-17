from django.apps import AppConfig


class SponsorConfig(AppConfig):
    """D17 (part): what an employer sponsor and a payer keep in their own space."""

    name = "modules.sponsor"
    label = "sponsor"

    def ready(self) -> None:
        # `manage.py rotate_field_keys` moves the sealed member numbers onto the current key.
        from foundation import rotation

        from .rotation import rotate

        rotation.register("sponsor", rotate)
