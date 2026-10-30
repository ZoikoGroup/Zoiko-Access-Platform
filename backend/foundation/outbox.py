"""Transactional outbox: "state changed" and "event queued" become ONE atomic fact.

Writing side   emit(...)        call it inside the same transaction as the change.
Delivery side  relay_once(...)  a worker that sends pending events to a publisher, at least once.

Guarantees
- If the transaction rolls back, the event never existed. If it commits, the event will be sent.
- Delivery is AT LEAST ONCE: a crash after sending but before recording means it is sent again.
  Consumers must therefore be safe against duplicates (see inbox.py).
- Events with the same ordering_key are delivered in order. An earlier event that is still
  pending, retrying or quarantined holds back later ones with the same key.
- Several relays can run at once: rows are claimed with FOR UPDATE SKIP LOCKED.
- Nothing about the failure except the exception CLASS NAME is stored (messages can hold data).

Known limit: ordering is exact per ordering_key when changes to the same aggregate are
serialized (the services will lock the aggregate row). It is not a global total order.
"""
from __future__ import annotations

import datetime as dt
import re
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, connection, transaction
from django.db.models import Exists, OuterRef

from .models import OutboxEvent, OutboxState
from .payloads import check_reference_payload
from .request_context import get_request_id, new_request_id
from .timeutil import Clock, require_aware, utc_now

MAX_ATTEMPTS = 10
_EVENT_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,90}\.v[0-9]{1,3}$")
_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class OutboxError(RuntimeError):
    """The outbox was used in a way that would break its guarantees."""


def emit(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_ref: str,
    payload: dict[str, Any] | None = None,
    ordering_key: str | None = None,
    dedupe_key: str | None = None,
    causation_id: str = "",
    occurred_at: dt.datetime | None = None,
    schema_version: int = 1,
) -> OutboxEvent:
    """Record an event in the current transaction. Needs a bound tenant (see cell_context)."""
    if not connection.in_atomic_block:
        raise OutboxError("emit() must run inside the same transaction as the state change")
    if not _EVENT_TYPE.match(event_type):
        raise OutboxError("event_type must look like 'booking.partner.confirmed.v1'")
    for value in (aggregate_type, aggregate_ref):
        if not _NAME.match(value):
            raise OutboxError("aggregate_type and aggregate_ref must be plain identifiers")
    body = check_reference_payload(payload or {})
    request_id = get_request_id()
    event = OutboxEvent(
        event_type=event_type,
        schema_version=schema_version,
        aggregate_type=aggregate_type,
        aggregate_ref=aggregate_ref,
        ordering_key=ordering_key or f"{aggregate_type}:{aggregate_ref}",
        dedupe_key=dedupe_key or "",
        payload=body,
        correlation_id=new_request_id() if request_id == "-" else request_id,
        causation_id=causation_id,
        occurred_at=require_aware(occurred_at) if occurred_at else utc_now(),
    )
    if not dedupe_key:
        event.save()
        return event
    try:
        with transaction.atomic():  # savepoint: a duplicate must not poison the outer transaction
            event.save()
        return event
    except IntegrityError:
        return OutboxEvent.objects.get(dedupe_key=dedupe_key)  # already emitted: same event


# --------------------------------------------------------------------------- delivery


@dataclass(frozen=True)
class PublishedEvent:
    """What a publisher receives. No database objects, so publishers stay simple to test."""

    event_id: uuid.UUID
    event_type: str
    schema_version: int
    aggregate_type: str
    aggregate_ref: str
    ordering_key: str
    correlation_id: str
    causation_id: str
    occurred_at: dt.datetime
    cell_id: str
    tenant_id: uuid.UUID
    payload: dict[str, Any]


Publisher = Callable[[PublishedEvent], None]


@dataclass(frozen=True)
class RelayResult:
    published: int
    retrying: int
    quarantined: int


def backoff_seconds(attempts: int, jitter: float | None = None) -> float:
    """5s, 10s, 20s ... capped at 1 hour, with +-20% jitter so retries do not arrive in a herd."""
    base = float(min(3600, 5 * 2 ** max(attempts - 1, 0)))
    if jitter is None:
        jitter = 0.8 + secrets.SystemRandom().random() * 0.4
    return base * jitter


def _to_published(row: OutboxEvent) -> PublishedEvent:
    return PublishedEvent(
        event_id=row.event_id,
        event_type=row.event_type,
        schema_version=row.schema_version,
        aggregate_type=row.aggregate_type,
        aggregate_ref=row.aggregate_ref,
        ordering_key=row.ordering_key,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        occurred_at=row.occurred_at,
        cell_id=row.cell_id,
        tenant_id=row.tenant_id,
        payload=row.payload,
    )


def relay_once(
    publisher: Publisher, *, batch_size: int = 50, clock: Clock | None = None
) -> RelayResult:
    """Deliver due events once. Run this from a worker in a loop."""
    now = utc_now(clock)
    published = retrying = quarantined = 0
    with transaction.atomic():
        # An event is held back while an EARLIER event with the same key is not yet published.
        blocked_by_earlier = OutboxEvent.objects.filter(
            ordering_key=OuterRef("ordering_key"),
            seq__lt=OuterRef("seq"),
            state__in=[OutboxState.PENDING, OutboxState.QUARANTINED],
        )
        batch = list(
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(state=OutboxState.PENDING, next_attempt_at__lte=now)
            .exclude(Exists(blocked_by_earlier))
            .order_by("seq")[:batch_size]
        )
        for row in batch:
            try:
                publisher(_to_published(row))
            except Exception as exc:  # noqa: BLE001 - any failure means "try again later"
                row.attempts += 1
                row.last_error = type(exc).__name__[:64]  # class only, never the message
                if row.attempts >= MAX_ATTEMPTS:
                    row.state = OutboxState.QUARANTINED
                    quarantined += 1
                else:
                    row.next_attempt_at = now + dt.timedelta(seconds=backoff_seconds(row.attempts))
                    retrying += 1
                row.save(update_fields=["attempts", "last_error", "state", "next_attempt_at"])
            else:
                row.state = OutboxState.PUBLISHED
                row.published_at = now
                row.save(update_fields=["state", "published_at"])
                published += 1
    return RelayResult(published, retrying, quarantined)


def backlog_stats(clock: Clock | None = None) -> dict[str, int | None]:
    """Delivery backlog for the whole cell (numbers only; the outbox is not tenant-scoped)."""
    now = utc_now(clock)
    pending = OutboxEvent.objects.filter(state=OutboxState.PENDING)
    oldest = pending.order_by("created_at").values_list("created_at", flat=True).first()
    return {
        "pending": pending.count(),
        "quarantined": OutboxEvent.objects.filter(state=OutboxState.QUARANTINED).count(),
        "oldest_pending_seconds": int((now - oldest).total_seconds()) if oldest else None,
    }
