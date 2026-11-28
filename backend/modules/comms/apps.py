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

        # Held partner requests that wait too long are emailed to the administrator (DEV-310).
        from .exchange_alerts import ESCALATION_EVERY_SECONDS, send_escalation_alerts

        periodic.register("comms.escalations", ESCALATION_EVERY_SECONDS, send_escalation_alerts)

        # Hourly: a clinic that asked for it gets its weekly summary once each week (DEV-392).
        from .report_email import REPORT_EVERY_SECONDS, send_weekly_reports

        periodic.register("comms.weekly_report", REPORT_EVERY_SECONDS, send_weekly_reports)

        # Every 30 seconds the worker sends text messages that are due (DEV-194).
        from foundation.texts import send_due_texts

        periodic.register("comms.texts", 30.0, lambda: send_due_texts())

        # A patient-record merge (DEV-365): these rows move to the surviving record.
        from foundation import person_refs

        from .models import Notification

        person_refs.register("comms.Notification", Notification)
