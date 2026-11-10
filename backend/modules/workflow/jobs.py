"""Keeps the work queue true to what is really waiting (a worker job, see foundation/periodic.py).

Items normally open and close from events. This job also opens an item for anything that is waiting
but has none: work that existed before the queue did, or an event that was lost. It only OPENS items
(an item that exists is left alone); closing stays with the events.
"""
from __future__ import annotations

import datetime as dt

from foundation.cell_context import tenant_transaction
from modules.messaging import api as messaging
from modules.referral import api as referral
from modules.scheduling import api as scheduling
from modules.trust import api as trust

from .engine import engine
from .models import Kind
from .services import open_item

SYNC_EVERY_SECONDS = 600.0
TIMERS_EVERY_SECONDS = 30.0


def sync_tenant() -> int:
    """Open the missing items of the bound customer. Returns how many things it looked at that
    are waiting (not how many it opened: opening twice changes nothing)."""
    seen = 0
    for org in trust.organization_names():
        target = dt.timedelta(hours=trust.response_target_hours(org))
        for ref, since in scheduling.waiting_requests(org):
            open_item(organization_ref=org, kind=Kind.CONFIRM_BOOKING, subject_ref=ref,
                      due_at=since + target)
            seen += 1
        for ref, since in messaging.unanswered_threads(org):
            open_item(organization_ref=org, kind=Kind.ANSWER_MESSAGE, subject_ref=ref,
                      due_at=since + target)
            seen += 1
        for r in referral.list_referrals(destination_organization_ref=org):
            if r["state"] == "issued":
                open_item(organization_ref=org, kind=Kind.DECIDE_REFERRAL,
                          subject_ref=r["public_id"],
                          due_at=dt.datetime.fromisoformat(r["created_at"]) + target)
                seen += 1
    return seen


def sync_all_tenants() -> int:
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            total += sync_tenant()
    return total


def fire_timers_all_tenants() -> int:
    """Raise an exception for every journey deadline that has run out, for every active customer."""
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            total += engine.fire_due_timers()
    return total
