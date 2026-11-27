from django.apps import AppConfig


class StagesConfig(AppConfig):
    """The stored lifecycle steps of bookings and referrals (DEV-360)."""

    name = "modules.stages"
    label = "stages"

    def ready(self) -> None:
        from .subscribers import register_subscribers

        register_subscribers()
