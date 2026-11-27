from django.apps import AppConfig


class DeveloperConfig(AppConfig):
    """GHE-13: the partner and developer surface: API clients, signed webhooks, the sandbox."""

    name = "modules.developer"
    label = "developer"

    def ready(self) -> None:
        from foundation import periodic

        from .jobs import DELIVER_EVERY_SECONDS, deliver_all_tenants
        from .subscribers import register_subscribers

        register_subscribers()
        periodic.register("developer.deliver", DELIVER_EVERY_SECONDS, deliver_all_tenants)
