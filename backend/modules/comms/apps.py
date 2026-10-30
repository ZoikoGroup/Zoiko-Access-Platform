from django.apps import AppConfig


class CommsConfig(AppConfig):
    """D16: Messaging and collaboration: intents, messages, delivery attempts and receipts."""

    name = "modules.comms"
    label = "comms"

    def ready(self) -> None:
        # The worker delivers booking events to this module's notifier.
        from .subscribers import register_subscribers

        register_subscribers()
