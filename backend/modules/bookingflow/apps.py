from django.apps import AppConfig


class BookingflowConfig(AppConfig):
    """Joins scheduling, the workflow engine and partner connectors: a booking can be confirmed by
    the clinic's own booking system. Owns no tables."""

    name = "modules.bookingflow"
    label = "bookingflow"

    def ready(self) -> None:
        from .subscribers import register_subscribers

        register_subscribers()
