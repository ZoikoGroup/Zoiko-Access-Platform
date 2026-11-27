"""Starts the standard journeys when something happens (DEV-395, decided 27 Nov 2026): a referral
arriving at a clinic, or a person joining a clinic's waiting list. Runs in the event-delivery
worker, in the EVENT's tenant. Events carry only references; everything else is read here.

Nothing starts unless the clinic published that journey (Workflow engine page, "Standard
journeys"). Who is accountable:
  * a referral: the doctor the clinic named to answer referrals (DEV-109), else the administrator
    who published the journey;
  * a waiting-list entry: the administrator who published the journey (they can hand it on).
"""
from __future__ import annotations

from foundation.dispatch import subscribe, subscriptions
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import PublishedEvent
from modules.referral import api as referral
from modules.waitlist import api as waitlist

from .definitions import TEMPLATES
from .engine import start_automatically

REFERRALS = "orchestration.referral_journey"
WAITLIST = "orchestration.waitlist_journey"


def on_referral_issued(event: PublishedEvent) -> None:
    ref = str(event.payload.get("referral_ref", ""))
    try:
        found = referral.get_referral(ref)
    except ZoikoError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return
        raise
    if found["state"] != "issued":
        return                                       # already answered: nothing to track
    org = found["destination_organization_ref"]
    inbox = referral.inbox_dict(org) or {}
    start_automatically(definition_key=TEMPLATES["referral"]["key"], subject_ref=ref,
                        organization_ref=org, owner_ref=str(inbox.get("owner_ref", "")))


def on_waitlist_joined(event: PublishedEvent) -> None:
    ref = str(event.payload.get("entry_ref", ""))
    try:
        found = waitlist.get_entry(ref)
    except ZoikoError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return
        raise
    if found["state"] != "waiting":
        return
    start_automatically(definition_key=TEMPLATES["waitlist"]["key"], subject_ref=ref,
                        organization_ref=found["organization_ref"], owner_ref="")


def register_subscribers() -> None:
    """Idempotent: safe to call at start-up and again from tests."""
    current = {s.consumer for s in subscriptions()}
    if REFERRALS not in current:
        subscribe(REFERRALS, "referral.issued.v1", on_referral_issued)
    if WAITLIST not in current:
        subscribe(WAITLIST, "waitlist.joined.v1", on_waitlist_joined)
