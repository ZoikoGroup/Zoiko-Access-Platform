"""Writing and checking the audit chain."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from django.db import connection, transaction
from django.db.models import QuerySet

from foundation.cell_context import CellContext, require_context
from foundation.payloads import check_reference_payload
from foundation.request_context import get_request_id
from foundation.timeutil import UTC, Clock, utc_now

from .models import GENESIS_HASH, AuditEvent, AuditOutcome

_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.]{0,99}$")


class AuditError(RuntimeError):
    """The audit log was used in a way that would break its guarantees."""


def _chain(ctx: CellContext) -> QuerySet[AuditEvent]:
    """This tenant's events. Filtered here as well as by row-level security: two fences."""
    return AuditEvent.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)


def compute_hash(event: AuditEvent) -> str:
    """SHA-256 over every meaningful field plus the previous hash, in a fixed layout."""
    body = {
        "cell_id": event.cell_id,
        "tenant_id": str(event.tenant_id),
        "chain_seq": event.chain_seq,
        "occurred_at": event.occurred_at.astimezone(UTC).isoformat(),
        "actor_ref": event.actor_ref,
        "action": event.action,
        "object_type": event.object_type,
        "object_ref": event.object_ref,
        "outcome": event.outcome,
        "reason_code": event.reason_code,
        "request_id": event.request_id,
        "details": event.details,
        "prev_hash": event.prev_hash,
    }
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def record(
    *,
    actor_ref: str,
    action: str,
    object_type: str,
    object_ref: str,
    outcome: AuditOutcome,
    reason_code: str = "",
    details: dict[str, Any] | None = None,
    clock: Clock | None = None,
) -> AuditEvent:
    """Append one event to the current tenant's chain. Call inside the business transaction."""
    if not connection.in_atomic_block:
        raise AuditError("record() must run inside the same transaction as the action")
    for value in (actor_ref, object_type, object_ref):
        if not _NAME.match(value):
            raise AuditError("actor, object type and object ref must be plain identifiers")
    if not _ACTION.match(action):
        raise AuditError("action must look like 'booking.confirm'")
    if reason_code and not _NAME.match(reason_code):
        raise AuditError("reason code must be a plain identifier")
    ctx = require_context()

    with transaction.atomic():
        # One writer per tenant at a time, or two events could claim the same position.
        # The lock is released automatically at commit or rollback.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [f"audit:{ctx.cell_id}:{ctx.tenant_id}"],
            )
        last = _chain(ctx).order_by("-chain_seq").first()
        event = AuditEvent(
            chain_seq=1 if last is None else last.chain_seq + 1,
            occurred_at=utc_now(clock),
            actor_ref=actor_ref,
            action=action,
            object_type=object_type,
            object_ref=object_ref,
            outcome=outcome,
            reason_code=reason_code,
            request_id=get_request_id(),
            details=check_reference_payload(details or {}),
            prev_hash=GENESIS_HASH if last is None else last.event_hash,
        )
        # Fill in cell/tenant first so the hash covers them, then seal the row.
        event.cell_id, event.tenant_id = ctx.cell_id, ctx.tenant_id
        event.event_hash = compute_hash(event)
        event.save()
        return event


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    problem: str = ""


def verify_chain() -> ChainReport:
    """Re-compute every hash for the current tenant and report the first mismatch."""
    ctx = require_context()
    expected_prev, expected_seq, checked = GENESIS_HASH, 1, 0
    for event in _chain(ctx).order_by("chain_seq").iterator(chunk_size=500):
        checked += 1
        if event.chain_seq != expected_seq:
            return ChainReport(False, checked, expected_seq, "a row is missing")
        if event.prev_hash != expected_prev:
            return ChainReport(False, checked, event.chain_seq, "link to the previous row broken")
        if compute_hash(event) != event.event_hash:
            return ChainReport(False, checked, event.chain_seq, "row content was changed")
        expected_prev, expected_seq = event.event_hash, expected_seq + 1
    return ChainReport(True, checked)


def recent_events(limit: int = 50) -> list[dict[str, Any]]:
    """Newest events of the current tenant, as plain dicts (for screens and reports)."""
    ctx = require_context()
    rows = _chain(ctx).order_by("-chain_seq")[: max(1, min(limit, 200))]
    return [
        {
            "seq": e.chain_seq, "at": e.occurred_at.isoformat(), "actor": e.actor_ref,
            "action": e.action, "object": f"{e.object_type}:{e.object_ref}",
            "outcome": e.outcome, "reason": e.reason_code, "hash": e.event_hash[:12],
            "prev": e.prev_hash[:12],
        }
        for e in rows
    ]


def activity_summary(since: dt.datetime) -> dict[str, Any]:
    """Aggregate facts about this tenant's audit log (no rows): chain check, last entry, and
    the number of denied sign-ins since `since`."""
    ctx = require_context()
    events = _chain(ctx)
    last = events.order_by("-chain_seq").values_list("occurred_at", flat=True).first()
    report = verify_chain()
    return {
        "chain_ok": report.ok, "chain_checked": report.checked,
        "chain_bad_seq": report.first_bad_seq,
        "last_event_at": last.isoformat() if last else None,
        "failed_logins": events.filter(
            action="auth.login", outcome=AuditOutcome.DENIED, occurred_at__gte=since).count(),
    }
