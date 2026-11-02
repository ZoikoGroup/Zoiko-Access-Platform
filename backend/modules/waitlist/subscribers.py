"""Reacts to scheduling events. Runs in the event-delivery worker, in the EVENT's tenant.

The events carry only ids; everything else is read here.
"""
from __future__ import annotations

from foundation.dispatch import subscribe, subscriptions
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import PublishedEvent
from modules.scheduling import api as scheduling

from .services import close_for_booking, offer_open_times

SLOTS_CONSUMER = "waitlist.slots"
BOOKINGS_CONSUMER = "waitlist.bookings"


def on_slot_event(event: PublishedEvent) -> None:
    if event.event_type == "slot.opened.v1":
        offer_open_times(str(event.payload["service_ref"]))


def on_booking_event(event: PublishedEvent) -> None:
    """A patient who booked the service is no longer waiting for it."""
    if event.event_type not in ("booking.requested.v1", "booking.rescheduled.v1"):
        return
    try:
        booking = scheduling.get_booking(str(event.payload["booking_ref"]))
    except ZoikoError as exc:
        if exc.code != ErrorCode.NOT_FOUND:
            raise
        return
    close_for_booking(booking["person_ref"], booking["slot"]["service_ref"])


def register_subscribers() -> None:
    """Idempotent: safe to call at start-up and again from tests."""
    current = {s.consumer for s in subscriptions()}
    if SLOTS_CONSUMER not in current:
        subscribe(SLOTS_CONSUMER, "slot.", on_slot_event)
    if BOOKINGS_CONSUMER not in current:
        subscribe(BOOKINGS_CONSUMER, "booking.", on_booking_event)
