"""The history of a work item: opened, taken, given back, done. Kept for good and chained.

A step is written in the SAME transaction as the change it describes. Items opened before this
existed have no history (nothing is invented for them).
"""
from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now

from .models import ItemEventType, WorkItem, WorkItemEvent


def _parts(row: WorkItemEvent) -> list[Any]:
    return [row.item.public_id, row.event_type, row.status_after, row.actor_ref,
            row.occurred_at.isoformat()]


def log_event(
    item: WorkItem, event_type: str, *, actor_ref: str, clock: Clock | None = None
) -> None:
    """Add the next step to this item's history. Call it inside the transaction of the change."""
    if event_type not in ItemEventType.values:
        raise ValueError("unknown work item event")
    ctx = require_context()
    scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
    last = WorkItemEvent.objects.filter(item=item, **scope).order_by("-seq").first()
    seq = last.seq + 1 if last else 1
    prev = last.record_digest if last else GENESIS
    now = utc_now(clock)
    digest = chain_digest(prev, seq, item.public_id, event_type, item.status, actor_ref,
                          now.isoformat())
    try:
        with transaction.atomic():
            WorkItemEvent.objects.create(
                item=item, seq=seq, event_type=event_type, status_after=item.status,
                actor_ref=actor_ref, occurred_at=now, prev_digest=prev, record_digest=digest)
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None    # another step got this place


def item_history(item_ref: str, organization_ref: str) -> dict[str, Any]:
    """The steps of one work item of this clinic, oldest first, and whether the chain still checks
    out. The caller has authorized the read."""
    ctx = require_context()
    scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
    item = WorkItem.objects.filter(
        public_id=item_ref, organization_ref=organization_ref, **scope).first()
    if item is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    rows = list(WorkItemEvent.objects.select_related("item").filter(
        item=item, **scope).order_by("seq"))
    ok, bad = verify_chain(rows, _parts)
    return {
        "item_ref": item.public_id, "kind": item.kind, "status": item.status,
        "chain_ok": ok, "bad_seq": bad,
        "events": [
            {"seq": r.seq, "event_type": r.event_type, "status_after": r.status_after,
             "actor_ref": r.actor_ref, "occurred_at": r.occurred_at.isoformat()}
            for r in rows],
    }
