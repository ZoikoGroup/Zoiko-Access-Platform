from django.apps import AppConfig


class OrchestrationConfig(AppConfig):
    """D05 kernel (GHE-12): workflow definitions, instances, evidence-gated transitions, timers.

    Separate from `modules.workflow` (the staff work queue), which it does not touch."""

    name = "modules.orchestration"
    label = "orchestration"

    def ready(self) -> None:
        # The worker checks for timers that ran out every 30 seconds.
        from foundation import periodic

        from .engine import fire_all_tenants

        periodic.register("orchestration.timers", 30.0, fire_all_tenants)
