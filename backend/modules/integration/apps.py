from django.apps import AppConfig


class IntegrationConfig(AppConfig):
    """GHE-13: outbound signed webhooks for partners and customers."""

    name = "modules.integration"
    label = "integration"

    def ready(self) -> None:
        from foundation import rotation

        from .rotation import rotate
        from .services import register_subscribers

        register_subscribers()
        rotation.register("integration", rotate)

        # Hourly: try again FHIR messages held over a code (DEV-338).
        from foundation import periodic

        from .jobs_retry import RETRY_EVERY_SECONDS, retry_all_tenants

        periodic.register("integration.fhir_retry", RETRY_EVERY_SECONDS, retry_all_tenants)
