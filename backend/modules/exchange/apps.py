from django.apps import AppConfig


class ExchangeConfig(AppConfig):
    """The status exchange between separate customers (references and status only)."""

    name = "modules.exchange"
    label = "exchange"

    def ready(self) -> None:
        # Once an hour the worker erases names whose agreement has run out.
        from foundation import periodic

        from .jobs import erase_expired_all, escalate_holds_all, scan_attachments_all

        periodic.register("exchange.erase_expired", 3600.0, erase_expired_all)
        periodic.register("exchange.escalate_holds", 3600.0, escalate_holds_all)
        periodic.register("exchange.scan_attachments", 60.0, scan_attachments_all)

        # A patient-record merge (DEV-365): these rows never move, they are only counted.
        from foundation import person_refs

        from .models import ExchangeRequest

        person_refs.keep("exchange.ExchangeRequest", ExchangeRequest)
