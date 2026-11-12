from django.apps import AppConfig


class TrustConfig(AppConfig):
    """D01: Trust, parties and tenancy: orgs, actors, persons, roles, delegation."""

    name = "modules.trust"
    label = "trust"

    def ready(self) -> None:
        # Hourly: a patient whose identity check has expired stops showing as verified.
        from foundation import periodic

        from .identity_jobs import REFRESH_EVERY_SECONDS, refresh_all_tenants

        periodic.register("trust.identity_expiry", REFRESH_EVERY_SECONDS, refresh_all_tenants)
