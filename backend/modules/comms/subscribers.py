"""Turns booking events into notifications for the patient.

Runs inside the event-delivery worker, in the EVENT's tenant. The event only says WHICH booking
changed; everything shown to the patient is read from the booking here, so no personal detail
ever travels in an event payload.
"""
from __future__ import annotations

import datetime as dt

from foundation.dispatch import subscribe, subscriptions
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import PublishedEvent
from modules.catalog import api as catalog
from modules.referral import api as referral
from modules.scheduling import api as scheduling
from modules.trust import api as trust

from .models import Notification

CONSUMER = "comms.notifier"

_KINDS = {
    "booking.requested.v1": "booking_requested",
    "booking.confirmed.v1": "booking_confirmed",
    "booking.cancelled.v1": "booking_cancelled",
}


def _when(local_start: str) -> str:
    return dt.datetime.fromisoformat(local_start).strftime("%a %d %b %Y, %H:%M")


def on_booking_event(event: PublishedEvent) -> None:
    kind = _KINDS.get(event.event_type)
    if kind is None:
        return
    try:
        booking = scheduling.get_booking(str(event.payload["booking_ref"]))
    except (KeyError, ZoikoError) as exc:
        if isinstance(exc, ZoikoError) and exc.code != ErrorCode.NOT_FOUND:
            raise
        return                                   # the booking no longer exists: nothing to say
    slot = booking["slot"]
    service = catalog.get_service(slot["service_ref"])["name"]
    org = (trust.get_organization(slot["organization_ref"]) or {}).get("name", "the clinic")
    when = f"{_when(slot['local_start'])} ({slot['local_zone']})"
    title, body = {
        "booking_requested": (
            "Appointment requested",
            f"Your request for {service} at {org} on {when} was sent. The clinic will confirm it."),
        "booking_confirmed": (
            "Appointment confirmed", f"{org} confirmed your {service} on {when}."),
        "booking_cancelled": (
            "Appointment cancelled", f"Your {service} on {when} at {org} was cancelled."),
    }[kind]
    Notification.objects.create(
        person_ref=booking["person_ref"], kind=kind, title=title, body=body[:300],
        booking_ref=booking["public_id"], source_event_id=event.event_id)


REFERRAL_CONSUMER = "comms.referral_notifier"
_REFERRAL_STATES = {
    "issued": ("referral_issued", "Referral sent"),
    "accepted": ("referral_accepted", "Referral accepted"),
    "needs_info": ("referral_needs_info", "Referral needs more information"),
    "declined": ("referral_declined", "Referral declined"),
}


def on_referral_event(event: PublishedEvent) -> None:
    """One notification per referral change. The wording is read from the referral here."""
    try:
        found = referral.get_referral(str(event.payload["referral_ref"]))
    except (KeyError, ZoikoError) as exc:
        if isinstance(exc, ZoikoError) and exc.code != ErrorCode.NOT_FOUND:
            raise
        return
    # The state AT THE TIME of the event (it carries no personal detail), not the current one:
    # if the worker is behind, each step must still be told as it happened.
    state = str(event.payload.get("state") or found["state"])
    kind, title = _REFERRAL_STATES.get(state, ("", ""))
    if not kind:
        return
    service = catalog.get_service(found["service_ref"])["name"]
    org = (trust.get_organization(found["destination_organization_ref"]) or {}).get(
        "name", "the clinic")
    body = {
        "issued": f"Your doctor referred you to {service} at {org}. They will review it.",
        "accepted": f"{org} accepted your referral to {service}.",
        "needs_info": f"{org} needs more information about your referral to {service}.",
        "declined": f"{org} could not accept your referral to {service}. Ask your doctor.",
    }[state]
    Notification.objects.create(
        person_ref=found["person_ref"], kind=kind, title=title, body=body[:300],
        source_event_id=event.event_id)


def register_subscribers() -> None:
    """Idempotent: safe to call at start-up and again from tests."""
    if not any(s.consumer == CONSUMER for s in subscriptions()):
        subscribe(CONSUMER, "booking.", on_booking_event)
    if not any(s.consumer == REFERRAL_CONSUMER for s in subscriptions()):
        subscribe(REFERRAL_CONSUMER, "referral.", on_referral_event)

