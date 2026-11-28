"""Keeps the work queue true to what is really waiting (a worker job, see foundation/periodic.py).

Items normally open and close from events. This job also opens an item for anything that is waiting
but has none: work that existed before the queue did, or an event that was lost. It only OPENS items
(an item that exists is left alone); closing stays with the events.
"""
from __future__ import annotations

import datetime as dt

from foundation.cell_context import tenant_transaction
from modules.catalog import api as catalog
from modules.messaging import api as messaging
from modules.referral import api as referral
from modules.rights import api as rights
from modules.scheduling import api as scheduling
from modules.trust import api as trust

from .models import Kind
from .services import open_item

SYNC_EVERY_SECONDS = 600.0


def sync_tenant() -> int:
    """Open the missing items of the bound customer. Returns how many things it looked at that
    are waiting (not how many it opened: opening twice changes nothing)."""
    seen = 0
    for org in trust.organization_names():
        # Due at the clinic's response target, counting only its opening hours (DEV-367).
        for ref, since in scheduling.waiting_requests(org):
            open_item(organization_ref=org, kind=Kind.CONFIRM_BOOKING, subject_ref=ref,
                      due_at=catalog.response_due(org, since))
            seen += 1
        for ref, since in messaging.unanswered_threads(org):
            open_item(organization_ref=org, kind=Kind.ANSWER_MESSAGE, subject_ref=ref,
                      due_at=catalog.response_due(org, since))
            seen += 1
        for r in referral.list_referrals(destination_organization_ref=org):
            if r["state"] == "issued":
                open_item(organization_ref=org, kind=Kind.DECIDE_REFERRAL,
                          subject_ref=r["public_id"],
                          due_at=catalog.response_due(
                              org, dt.datetime.fromisoformat(r["created_at"])))
                seen += 1
        for c in rights.list_cases(organization_ref=org):
            if c["state"] in ("received", "in_progress"):
                due = (dt.datetime.fromisoformat(c["due_at"]) if c["due_at"]
                       else catalog.response_due(org, dt.datetime.fromisoformat(c["created_at"])))
                open_item(organization_ref=org, kind=Kind.ANSWER_DATA_REQUEST,
                          subject_ref=c["public_id"], due_at=due)
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
