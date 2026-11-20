"""Reacts to connector events. Runs in the event-delivery worker, in the EVENT's tenant.

The events carry only ids; everything else is read here.
"""
from __future__ import annotations

from foundation.dispatch import subscribe, subscriptions
from foundation.outbox import PublishedEvent

from .flow import on_provider_answer

CONNECTOR = "bookingflow.connector"


def on_connector_event(event: PublishedEvent) -> None:
    operation = str(event.payload.get("operation_ref", ""))
    if not operation:
        return
    if event.event_type == "connector.operation.confirmed.v1":
        on_provider_answer(operation, confirmed=True)
    elif event.event_type == "connector.operation.rejected.v1":
        on_provider_answer(operation, confirmed=False)


def register_subscribers() -> None:
    """Idempotent: safe to call at start-up and again from tests."""
    current = {s.consumer for s in subscriptions()}
    if CONNECTOR not in current:
        subscribe(CONNECTOR, "connector.operation.", on_connector_event)
