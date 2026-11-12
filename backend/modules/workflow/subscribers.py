"""Opens and closes work items from the events of the other modules.

Runs in the event-delivery worker, in the event's tenant. An event only says WHICH booking,
referral or conversation changed; everything else is read from the module here, so no personal
detail travels in an event. Each handler is safe to run twice.
"""
from __future__ import annotations

import datetime as dt

from foundation.dispatch import subscribe, subscriptions
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import PublishedEvent
from foundation.timeutil import utc_now
from modules.messaging import api as messaging
from modules.referral import api as referral
from modules.rights import api as rights
from modules.scheduling import api as scheduling
from modules.trust import api as trust

from .models import Kind, Status
from .services import close_item, open_item

BOOKING_CONSUMER = "workflow.booking_queue"
REFERRAL_CONSUMER = "workflow.referral_queue"
MESSAGE_CONSUMER = "workflow.message_queue"
RIGHTS_CONSUMER = "workflow.rights_queue"


def _due(organization_ref: str) -> dt.datetime:
    """When work for this clinic is due: its own response target from now."""
    return utc_now() + dt.timedelta(hours=trust.response_target_hours(organization_ref))


def _gone(exc: ZoikoError) -> None:
    """The booking, referral or thread no longer exists: nothing to open. Anything else is real."""
    if exc.code != ErrorCode.NOT_FOUND:
        raise exc


def _closer(event: PublishedEvent) -> str:
    """Who closed the work, from the event: "patient" when the patient side acted, else the clinic
    login it names, else "system"."""
    if event.payload.get("side") == "patient":
        return "patient"
    return str(event.payload.get("actor_ref", "")) or "system"


def on_booking_event(event: PublishedEvent) -> None:
    ref = str(event.payload.get("booking_ref", ""))
    try:
        if event.event_type == "booking.requested.v1":
            booking = scheduling.get_booking(ref)
            if booking["status"] == "requested":
                open_item(organization_ref=booking["slot"]["organization_ref"],
                          kind=Kind.CONFIRM_BOOKING, subject_ref=booking["public_id"],
                          due_at=_due(booking["slot"]["organization_ref"]))
        elif event.event_type == "booking.confirmed.v1":
            close_item(kind=Kind.CONFIRM_BOOKING, subject_ref=ref, actor_ref=_closer(event))
        elif event.event_type == "booking.cancelled.v1":
            close_item(kind=Kind.CONFIRM_BOOKING, subject_ref=ref, status=Status.CANCELLED,
                       actor_ref=_closer(event))
    except ZoikoError as exc:
        _gone(exc)


def on_referral_event(event: PublishedEvent) -> None:
    ref = str(event.payload.get("referral_ref", ""))
    try:
        if event.event_type == "referral.issued.v1":
            found = referral.get_referral(ref)
            if found["state"] == "issued":
                open_item(organization_ref=found["destination_organization_ref"],
                          kind=Kind.DECIDE_REFERRAL, subject_ref=found["public_id"],
                          due_at=_due(found["destination_organization_ref"]))
        elif event.event_type == "referral.decided.v1":
            close_item(kind=Kind.DECIDE_REFERRAL, subject_ref=ref, actor_ref=_closer(event))
    except ZoikoError as exc:
        _gone(exc)


def on_message_event(event: PublishedEvent) -> None:
    if event.event_type != "message.sent.v1":
        return
    ref = str(event.payload.get("thread_ref", ""))
    try:
        if event.payload.get("side") == "patient":
            thread = messaging.get_thread(ref)
            open_item(organization_ref=thread["organization_ref"], kind=Kind.ANSWER_MESSAGE,
                      subject_ref=thread["public_id"],
                      due_at=_due(thread["organization_ref"]))
        else:
            close_item(kind=Kind.ANSWER_MESSAGE, subject_ref=ref, actor_ref=_closer(event))
    except ZoikoError as exc:
        _gone(exc)


def on_rights_event(event: PublishedEvent) -> None:
    """A data request opens work for the clinic, due on the date a country pack gave it (or the
    clinic's own response target when none did), and closes it when the request ends."""
    ref = str(event.payload.get("case_ref", ""))
    try:
        if event.event_type == "rights.requested.v1":
            case = rights.get_case(ref)
            if case["state"] in ("received", "in_progress"):
                due = (dt.datetime.fromisoformat(case["due_at"]) if case["due_at"]
                       else _due(case["organization_ref"]))
                open_item(organization_ref=case["organization_ref"],
                          kind=Kind.ANSWER_DATA_REQUEST, subject_ref=case["public_id"],
                          due_at=due)
        elif event.event_type == "rights.withdrawn.v1":
            close_item(kind=Kind.ANSWER_DATA_REQUEST, subject_ref=ref, status=Status.CANCELLED,
                       actor_ref="patient")
        elif event.event_type == "rights.closed.v1":
            close_item(kind=Kind.ANSWER_DATA_REQUEST, subject_ref=ref, actor_ref=_closer(event))
    except ZoikoError as exc:
        _gone(exc)


def register_subscribers() -> None:
    """Idempotent: safe to call at start-up and again from tests."""
    for consumer, prefix, handler in (
        (BOOKING_CONSUMER, "booking.", on_booking_event),
        (REFERRAL_CONSUMER, "referral.", on_referral_event),
        (MESSAGE_CONSUMER, "message.", on_message_event),
        (RIGHTS_CONSUMER, "rights.", on_rights_event),
    ):
        if not any(s.consumer == consumer for s in subscriptions()):
            subscribe(consumer, prefix, handler)
