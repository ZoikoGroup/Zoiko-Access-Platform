from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    """D04: Access and scheduling: booking intents, availability, partner bookings."""

    name = "modules.scheduling"
    label = "scheduling"

    def ready(self) -> None:
        from foundation import periodic

        from .jobs import REMIND_EVERY_SECONDS, remind_all_tenants

        periodic.register("scheduling.reminders", REMIND_EVERY_SECONDS, remind_all_tenants)

        from foundation import retention

        from .retention import count_older

        retention.register("bookings", "Appointments", "the appointment time", count_older)

        # A patient-record merge (DEV-365): these rows move to the surviving record.
        from foundation import person_refs

        from .models import Booking

        person_refs.register("scheduling.Booking", Booking)
