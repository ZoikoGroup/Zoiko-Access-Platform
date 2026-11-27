from django.apps import AppConfig


class WorkflowConfig(AppConfig):
    """D05: Workflow fabric: definitions, instances, transitions, tasks, timers, exceptions.

    Built so far: the staff work queue (work items opened and closed by events) and the
    durable workflow engine (versioned definitions, instances, chained transitions, timers)."""

    name = "modules.workflow"
    label = "workflow"

    def ready(self) -> None:
        # The worker delivers booking, referral and message events to the work queue.
        from .subscribers import register_subscribers

        register_subscribers()

        # Every ten minutes the worker opens items for anything waiting that has none yet.
        from foundation import periodic

        from .jobs import (
            SYNC_EVERY_SECONDS,
            TIMERS_EVERY_SECONDS,
            fire_timers_all_tenants,
            sync_all_tenants,
        )

        periodic.register("workflow.sync", SYNC_EVERY_SECONDS, sync_all_tenants)
        periodic.register("workflow.timers", TIMERS_EVERY_SECONDS, fire_timers_all_tenants)
