"""Turns booking events into notifications for the patient.

Runs inside the event-delivery worker, in the EVENT's tenant. The event only says WHICH booking
changed; everything shown to the patient is read from the booking here, so no personal detail
ever travels in an event payload.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.conf import settings

from foundation.dispatch import subscribe, subscriptions
from foundation.errors import ErrorCode, ZoikoError
from foundation.mail import queue_email
from foundation.outbox import PublishedEvent
from modules.catalog import api as catalog
from modules.messaging import api as messaging
from modules.referral import api as referral
from modules.scheduling import api as scheduling
from modules.trust import api as trust
from modules.waitlist import api as waitlist

from .models import Notification
from .services import email_notices_enabled

CONSUMER = "comms.notifier"

_KINDS = {
    "booking.requested.v1": "booking_requested",
    "booking.confirmed.v1": "booking_confirmed",
    "booking.cancelled.v1": "booking_cancelled",
    "booking.rescheduled.v1": "booking_rescheduled",
    "booking.attendance.v1": "booking_attendance",
    "booking.reminder.v1": "booking_reminder",
}


_CLINIC_WHY = {
    "clinic_unavailable": "the clinic is closed or unavailable then",
    "doctor_unavailable": "the doctor is unavailable then",
    "service_changed": "the service has changed",
}


def _clinic_reason(booking: dict[str, Any]) -> str:
    """The clinic's reason in plain words, from a fixed list (never free text). A patient's own
    reason is not repeated back to them."""
    why = _CLINIC_WHY.get(booking.get("cancel_reason", ""))
    return f" The clinic said: {why}." if why else ""


# What an email may say. It names NOTHING about the care (no clinic, service, time or doctor):
# only that something changed, and where to look. Anyone reading over a shoulder or a forwarded
# message learns nothing; the details are behind the sign-in.
_EMAIL_HEADLINE = {
    "booking_confirmed": "An appointment was confirmed.",
    "booking_cancelled": "An appointment was cancelled.",
    "booking_rescheduled": "An appointment was moved.",
    "booking_reminder": "You have an appointment coming up.",
    "message_received": "You have a new message from your clinic.",
}


def _email_the_patient(person_ref: str, kind: str) -> None:
    """If this patient asked for emails, has a login of their own, and has confirmed their
    address, queue one short email. Never for guardians, never to an unconfirmed address."""
    headline = _EMAIL_HEADLINE.get(kind)
    if headline is None:
        return
    target = trust.verified_login_email_of_person(person_ref)
    if target is None:
        return
    actor_ref, address = target
    if not email_notices_enabled(actor_ref):
        return
    queue_email(
        kind="patient_update", to_address=address, subject="An update on Zoiko Access",
        body=(f"Hello,\n\n{headline}\n\nSign in to see the details: "
              f"{settings.PATIENT_APP_URL}/?page=notifications\n\n"
              "You are getting this because you turned on email updates. You can turn them off "
              "on the Notifications page.\n\nZoiko Access\n"))


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
    if kind == "booking_attendance":
        # Worded from what the EVENT recorded, so a late worker still tells each step as it was.
        kind = {"attended": "booking_attended", "no_show": "booking_no_show"}.get(
            str(event.payload.get("attendance")), "")
        if not kind:
            return
    if kind == "booking_reminder" and booking["status"] != "confirmed":
        return                                       # cancelled since: do not remind
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
            "Appointment cancelled",
            f"Your {service} on {when} at {org} was cancelled." + _clinic_reason(booking)),
        "booking_reminder": (
            "Appointment reminder", f"Reminder: your {service} at {org} is on {when}."),
        "booking_attended": (
            "Visit recorded", f"{org} recorded that you attended your {service} on {when}."),
        "booking_no_show": (
            "Missed appointment",
            f"{org} recorded that you did not attend your {service} on {when}. "
            "Contact the clinic to book again."),
        "booking_rescheduled": (
            "Appointment moved",
            f"Your {service} at {org} was moved to {when}." + (
                " It is confirmed." if booking["status"] == "confirmed"
                else " The clinic will confirm the new time.")),
    }[kind]
    Notification.objects.create(
        person_ref=booking["person_ref"], kind=kind, title=title, body=body[:300],
        booking_ref=booking["public_id"], source_event_id=event.event_id)
    _email_the_patient(booking["person_ref"], kind)


REFERRAL_CONSUMER = "comms.referral_notifier"
_REFERRAL_STATES = {
    "issued": ("referral_issued", "Referral sent"),
    "accepted": ("referral_accepted", "Referral accepted by the clinic"),
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


WAITLIST_CONSUMER = "comms.waitlist_notifier"


def on_waitlist_event(event: PublishedEvent) -> None:
    """Tell a waiting patient a time opened up. Skipped if they already booked or left."""
    if event.event_type != "waitlist.offered.v1":
        return
    try:
        entry = waitlist.get_entry(str(event.payload["entry_ref"]))
    except (KeyError, ZoikoError) as exc:
        if isinstance(exc, ZoikoError) and exc.code != ErrorCode.NOT_FOUND:
            raise
        return
    if entry["state"] != "offered":
        return
    service = catalog.get_service(entry["service_ref"])["name"]
    org = (trust.get_organization(entry["organization_ref"]) or {}).get("name", "the clinic")
    Notification.objects.create(
        person_ref=entry["person_ref"], kind="waitlist_offer", title="A time opened up",
        body=f"A time opened up for {service} at {org}. Book it soon, before it is taken."[:300],
        source_event_id=event.event_id)


FAMILY_CONSUMER = "comms.family_notifier"


def on_family_event(event: PublishedEvent) -> None:
    """Tell a patient the clinic decided on their request to act for a family member. The text
    names nobody: it is written in the patient's cell, not sent with the event."""
    if event.event_type != "family.decided.v1":
        return
    outcome = str(event.payload.get("outcome"))
    if outcome not in ("approved", "rejected"):
        return
    try:
        grant = trust.get_family_grant(str(event.payload["grant_ref"]))
    except (KeyError, ZoikoError) as exc:
        if isinstance(exc, ZoikoError) and exc.code != ErrorCode.NOT_FOUND:
            raise
        return
    own = trust.own_person_of(grant["delegate_ref"])
    if not own:
        return                                    # the requester no longer has a patient record
    title, body = {
        "approved": ("Family request approved",
                     "The clinic approved your request. You can now book and see appointments "
                     "for that person under Find Healthcare."),
        "rejected": ("Family request not approved",
                     "The clinic could not approve your request. Please speak to the clinic "
                     "and bring proof of identity."),
    }[outcome]
    Notification.objects.create(
        person_ref=own, kind=f"family_{outcome}", title=title, body=body,
        source_event_id=event.event_id)


MESSAGE_CONSUMER = "comms.message_notifier"


def on_message_event(event: PublishedEvent) -> None:
    """Tell a patient the clinic wrote to them. The notice never contains the message."""
    if event.event_type != "message.sent.v1" or event.payload.get("side") != "staff":
        return
    try:
        thread = messaging.get_thread(str(event.payload["thread_ref"]))
    except (KeyError, ZoikoError) as exc:
        if isinstance(exc, ZoikoError) and exc.code != ErrorCode.NOT_FOUND:
            raise
        return
    org = (trust.get_organization(thread["organization_ref"]) or {}).get("name", "the clinic")
    Notification.objects.create(
        person_ref=thread["person_ref"], kind="message_received", title="New message",
        body=f"{org} sent you a message. Open Messages to read it."[:300],
        source_event_id=event.event_id)
    _email_the_patient(thread["person_ref"], "message_received")


def register_subscribers() -> None:
    """Idempotent: safe to call at start-up and again from tests."""
    if not any(s.consumer == CONSUMER for s in subscriptions()):
        subscribe(CONSUMER, "booking.", on_booking_event)
    if not any(s.consumer == REFERRAL_CONSUMER for s in subscriptions()):
        subscribe(REFERRAL_CONSUMER, "referral.", on_referral_event)
    if not any(s.consumer == WAITLIST_CONSUMER for s in subscriptions()):
        subscribe(WAITLIST_CONSUMER, "waitlist.", on_waitlist_event)
    if not any(s.consumer == FAMILY_CONSUMER for s in subscriptions()):
        subscribe(FAMILY_CONSUMER, "family.", on_family_event)
    if not any(s.consumer == MESSAGE_CONSUMER for s in subscriptions()):
        subscribe(MESSAGE_CONSUMER, "message.", on_message_event)

