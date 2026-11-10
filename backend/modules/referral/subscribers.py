"""Reacts to a doctor losing their role. Runs in the event-delivery worker, in the EVENT's tenant.

The event carries only references (GHE-06 professional.privilege.revoked.v1).
"""
from __future__ import annotations

from foundation.dispatch import subscribe, subscriptions
from foundation.outbox import PublishedEvent

from .services import inbox_member_left

CONSUMER = "referral.privileges"


def on_privilege_event(event: PublishedEvent) -> None:
    """A doctor named to answer referrals who leaves the site must not stay named."""
    if event.event_type != "professional.privilege.revoked.v1":
        return
    if event.payload.get("role_code") != "R03":
        return
    inbox_member_left(str(event.payload["organization_ref"]), str(event.payload["actor_ref"]))


def register_subscribers() -> None:
    """Idempotent: safe to call at start-up and again from tests."""
    if CONSUMER not in {s.consumer for s in subscriptions()}:
        subscribe(CONSUMER, "professional.", on_privilege_event)
