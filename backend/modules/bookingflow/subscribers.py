"""Reacts to booking and connector events. Runs in the event-delivery worker, in the EVENT's tenant.
The events carry only ids; everything else is read here."""
from __future__ import annotations

from foundation.dispatch import subscribe, subscriptions
from foundation.outbox import PublishedEvent

from .flow import on_attendance, on_cancelled, on_provider_answer, on_requested

BOOKINGS = "bookingflow.bookings"
CONNECTOR = "bookingflow.connector"


def on_booking_event(event: PublishedEvent) -> None:
    ref = str(event.payload.get("booking_ref", ""))
    if not ref:
        return
    if event.event_type in ("booking.requested.v1", "booking.rescheduled.v1"):
        on_requested(ref)
    elif event.event_type == "booking.cancelled.v1":
        on_cancelled(ref, str(event.payload.get("actor_ref") or "patient"))
    elif event.event_type == "booking.attendance.v1":
        on_attendance(ref, str(event.payload.get("attendance", "")))


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
    if BOOKINGS not in current:
        subscribe(BOOKINGS, "booking.", on_booking_event)
    if CONNECTOR not in current:
        subscribe(CONNECTOR, "connector.operation.", on_connector_event)
