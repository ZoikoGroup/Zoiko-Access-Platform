from django.apps import AppConfig


class WorkflowConfig(AppConfig):
    """D05: Workflow fabric: definitions, instances, transitions, tasks, timers, exceptions.

    Built so far: the staff work queue (work items opened and closed by events)."""

    name = "modules.workflow"
    label = "workflow"

    def ready(self) -> None:
        # The worker delivers booking, referral and message events to the work queue.
        from .subscribers import register_subscribers

        register_subscribers()

        # Every ten minutes the worker opens items for anything waiting that has none yet.
        from foundation import periodic

        from .jobs import SYNC_EVERY_SECONDS, sync_all_tenants

        periodic.register("workflow.sync", SYNC_EVERY_SECONDS, sync_all_tenants)
