"""Booking and referral events: write the stage down. Runs in the event worker, in the event's
tenant. The events carry ids only; the stage is worked out from the record itself."""
from __future__ import annotations

from foundation.dispatch import subscribe, subscriptions
from foundation.outbox import PublishedEvent

from .services import note_booking, note_referral

BOOKINGS = "stages.bookings"
REFERRALS = "stages.referrals"


def on_booking_event(event: PublishedEvent) -> None:
    ref = event.payload.get("booking_ref")
    if ref:
        note_booking(str(ref), cause=event.event_type)


def on_referral_event(event: PublishedEvent) -> None:
    ref = event.payload.get("referral_ref")
    if ref:
        note_referral(str(ref), cause=event.event_type)


def register_subscribers() -> None:
    """Idempotent: safe to call at start-up and again from tests."""
    current = {s.consumer for s in subscriptions()}
    if BOOKINGS not in current:
        subscribe(BOOKINGS, "booking.", on_booking_event)
    if REFERRALS not in current:
        subscribe(REFERRALS, "referral.", on_referral_event)
