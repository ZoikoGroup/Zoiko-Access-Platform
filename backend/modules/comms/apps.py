from django.apps import AppConfig


class CommsConfig(AppConfig):
    """D16: Messaging and collaboration: intents, messages, delivery attempts and receipts."""

    name = "modules.comms"
    label = "comms"

    def ready(self) -> None:
        # The worker delivers booking events to this module's notifier.
        from .subscribers import register_subscribers

        register_subscribers()

        # Once an hour the worker checks for late work and emails clinic operations (once a day).
        from foundation import periodic

        from .digest import DIGEST_EVERY_SECONDS, send_late_digests

        periodic.register("comms.late_digest", DIGEST_EVERY_SECONDS, send_late_digests)
