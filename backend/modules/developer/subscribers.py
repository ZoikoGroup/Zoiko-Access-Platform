"""Turns the product's public events into webhook deliveries. Runs in the event-delivery worker, in
the EVENT's tenant. Only the families in PUBLIC_EVENT_PREFIXES are ever passed on."""
from __future__ import annotations

from foundation.dispatch import subscribe, subscriptions
from foundation.outbox import PublishedEvent

from .services import PUBLIC_EVENT_PREFIXES, fan_out

CONSUMER = "developer.webhooks."


def on_event(event: PublishedEvent) -> None:
    fan_out(event)


def register_subscribers() -> None:
    """Idempotent: safe to call at start-up and again from tests."""
    current = {s.consumer for s in subscriptions()}
    for prefix in PUBLIC_EVENT_PREFIXES:
        consumer = CONSUMER + prefix.rstrip(".")
        if consumer not in current:
            subscribe(consumer, prefix, on_event)
