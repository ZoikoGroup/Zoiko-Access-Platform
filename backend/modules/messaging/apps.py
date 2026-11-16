from django.apps import AppConfig


class MessagingConfig(AppConfig):
    """D16: Secure communications: message threads between a patient and their clinic."""

    name = "modules.messaging"
    label = "messaging"

    def ready(self) -> None:
        # `manage.py rotate_field_keys` moves this module's encrypted values onto the current key.
        from foundation import rotation

        from .rotation import rotate

        rotation.register("messaging", rotate)
