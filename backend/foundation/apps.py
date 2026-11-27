from django.apps import AppConfig


class FoundationConfig(AppConfig):
    """Shared plumbing: ids, time, errors, outbox, inbox, idempotency (built in B1-B2).

    Named `foundation`, not `platform`, because a top-level `platform` package would shadow
    Python's standard-library `platform` module and break imports.
    """

    name = "foundation"
    label = "foundation"

    def ready(self) -> None:
        # Every five minutes: forget the one-minute rate-limit counters that ended (DEV-387).
        from . import (
            periodic,
            release_check,  # noqa: F401  (registers the `check --deploy` check)
        )
        from .ratelimit import forget_old

        periodic.register("foundation.rate_windows", 300.0, forget_old)

        # Daily: forget the per-minute request counts older than 35 days (DEV-388).
        from .error_budget import forget_old as forget_old_minutes

        periodic.register("foundation.slo_minutes", 86400.0, forget_old_minutes)

        # Every minute: page the on-call person for an error-budget alert (DEV-400, PagerDuty).
        from .paging import page_budget_alerts

        periodic.register("foundation.paging", 60.0, page_budget_alerts)
