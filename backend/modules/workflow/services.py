"""Opening, closing and claiming work items. Other modules never call this: the work queue listens
to their events (subscribers.py), and the HTTP layer reads and claims through api.py."""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .history import log_event
from .models import ItemEventType, Kind, Status, WorkItem


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def item_dict(i: WorkItem, clock: Clock | None = None) -> dict[str, Any]:
    return {
        "public_id": i.public_id, "organization_ref": i.organization_ref, "kind": i.kind,
        "label": Kind(i.kind).label, "subject_ref": i.subject_ref, "status": i.status,
        "created_at": i.created_at.isoformat(), "due_at": i.due_at.isoformat(),
        "overdue": i.status == Status.OPEN and i.due_at < utc_now(clock),
        "assigned_to_ref": i.assigned_to_ref,
        "closed_at": i.closed_at.isoformat() if i.closed_at else None,
    }


def open_item(
    *, organization_ref: str, kind: str, subject_ref: str, due_at: dt.datetime
) -> None:
    """Open the work, once. Asking again for work that is already open changes nothing."""
    try:
        with transaction.atomic():
            item, created = WorkItem.objects.get_or_create(
                organization_ref=organization_ref, kind=kind, subject_ref=subject_ref,
                status=Status.OPEN, defaults={"due_at": due_at}, **_scope())
            if created:
                log_event(item, ItemEventType.OPENED, actor_ref="system")
    except IntegrityError:
        return                                              # another worker opened it first


def close_item(
    *, kind: str, subject_ref: str, status: str = Status.DONE, actor_ref: str = "system",
    clock: Clock | None = None,
) -> int:
    """Close the open work for this subject. Closing work that is not open changes nothing.

    `actor_ref` says who closed it in the real world: a clinic login, "patient" (when the patient
    side did, for example by cancelling) or "system" (nobody: it closed itself)."""
    now = utc_now(clock)
    with transaction.atomic():
        open_ones = list(WorkItem.objects.select_for_update().filter(
            kind=kind, subject_ref=subject_ref, status=Status.OPEN, **_scope()))
        for item in open_ones:
            item.status, item.closed_at = status, now
            item.entity_version += 1
            item.save(update_fields=["status", "closed_at", "entity_version", "updated_at"])
            log_event(item, ItemEventType.CANCELLED if status == Status.CANCELLED
                      else ItemEventType.DONE, actor_ref=actor_ref, clock=clock)
    return len(open_ones)


def list_items(
    organization_ref: str, *, assigned_to_ref: str = "", unassigned: bool = False,
    limit: int = 200, clock: Clock | None = None,
) -> list[dict[str, Any]]:
    """Open work of one clinic, the one due first at the top."""
    rows = WorkItem.objects.filter(
        organization_ref=organization_ref, status=Status.OPEN, **_scope())
    if assigned_to_ref:
        rows = rows.filter(assigned_to_ref=assigned_to_ref)
    if unassigned:
        rows = rows.filter(assigned_to_ref="")
    return [item_dict(i, clock) for i in rows.order_by("due_at")[: max(1, min(limit, 500))]]


MAX_CLOSED_DAYS = 30


def list_closed(
    organization_ref: str, days: int, *, limit: int = 200, clock: Clock | None = None
) -> list[dict[str, Any]]:
    """Work this clinic closed (done, or no longer needed) in the last `days` days (1 to 30), the
    newest first. Kinds of work and times only. The caller has authorized the read."""
    since = utc_now(clock) - dt.timedelta(days=max(1, min(days, MAX_CLOSED_DAYS)))
    rows = WorkItem.objects.filter(
        organization_ref=organization_ref, status__in=[Status.DONE, Status.CANCELLED],
        closed_at__gte=since, **_scope()).order_by("-closed_at")
    return [item_dict(i, clock) for i in rows[: max(1, min(limit, 500))]]


def _own(item_ref: str, organization_ref: str) -> WorkItem:
    found = WorkItem.objects.select_for_update().filter(
        public_id=item_ref, organization_ref=organization_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def claim(item_ref: str, organization_ref: str, actor_public_id: str) -> dict[str, Any]:
    """Take an open item. Taking one somebody else holds is refused (they must release it)."""
    with transaction.atomic():
        item = _own(item_ref, organization_ref)
        if item.status != Status.OPEN or item.assigned_to_ref not in ("", actor_public_id):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if item.assigned_to_ref != actor_public_id:
            item.assigned_to_ref = actor_public_id
            item.entity_version += 1
            item.save(update_fields=["assigned_to_ref", "entity_version", "updated_at"])
            record(actor_ref=actor_public_id, action="workitem.claim", object_type="workitem",
                   object_ref=item.public_id, outcome=AuditOutcome.ALLOWED)
            log_event(item, ItemEventType.CLAIMED, actor_ref=actor_public_id)
        return item_dict(item)


def release(item_ref: str, organization_ref: str, actor_public_id: str) -> dict[str, Any]:
    """Give an item back. Only the person holding it can."""
    with transaction.atomic():
        item = _own(item_ref, organization_ref)
        if item.status != Status.OPEN or item.assigned_to_ref != actor_public_id:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        item.assigned_to_ref = ""
        item.entity_version += 1
        item.save(update_fields=["assigned_to_ref", "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="workitem.release", object_type="workitem",
               object_ref=item.public_id, outcome=AuditOutcome.ALLOWED)
        log_event(item, ItemEventType.RELEASED, actor_ref=actor_public_id)
        return item_dict(item)
