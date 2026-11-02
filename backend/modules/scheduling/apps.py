from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    """D04: Access and scheduling: booking intents, availability, partner bookings."""

    name = "modules.scheduling"
    label = "scheduling"

    def ready(self) -> None:
        from foundation import periodic

        from .jobs import REMIND_EVERY_SECONDS, remind_all_tenants

        periodic.register("scheduling.reminders", REMIND_EVERY_SECONDS, remind_all_tenants)
