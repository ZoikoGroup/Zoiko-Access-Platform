from django.apps import AppConfig


class WaitlistConfig(AppConfig):
    """D04: Access and scheduling: the waiting list for services with no free time."""

    name = "modules.waitlist"
    label = "waitlist"

    def ready(self) -> None:
        # The worker delivers slot and booking events here, and runs the expiry job.
        from foundation import periodic

        from .jobs import EXPIRE_EVERY_SECONDS, expire_all_tenants
        from .subscribers import register_subscribers

        register_subscribers()
        periodic.register("waitlist.expire_offers", EXPIRE_EVERY_SECONDS, expire_all_tenants)
