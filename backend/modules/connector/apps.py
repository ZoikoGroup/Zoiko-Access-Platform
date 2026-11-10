from django.apps import AppConfig


class ConnectorConfig(AppConfig):
    """GHE-13 / GHE-22: partner connectors. Operations Zoiko asks an outside system to perform,
    kept honest about what the partner actually said."""

    name = "modules.connector"
    label = "connector"

    def ready(self) -> None:
        from django.conf import settings

        from foundation import periodic

        from . import adapters
        from .jobs import RUN_EVERY_SECONDS, run_all_tenants
        from .simulator import simulator

        # The pretend partner exists only where a developer asked for it (never in production).
        if getattr(settings, "CONNECTOR_SIMULATOR", False):
            adapters.register(simulator)
        periodic.register("connector.run", RUN_EVERY_SECONDS, run_all_tenants)
