"""Who receives each event: a small in-process subscriber registry plus the delivery function the
relay worker uses.

    subscribe("notifications", "booking.", handler)     # at start-up, from a module

    dispatch(event)    # called by relay_once for every due event

For each subscriber whose prefix matches the event type, `dispatch`
  1. binds the EVENT's tenant (never anything from the payload),
  2. runs the handler inside the inbox's "once per consumer per event" guard, in a savepoint.

So a handler that fails changes nothing and the event is retried later; a subscriber that already
succeeded is not run again on the retry; and a duplicate delivery changes nothing.

An event nobody subscribes to is simply marked delivered: the outbox is a delivery guarantee, not
an archive (the audit log is the record).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from .cell_context import build_context, tenant_transaction
from .inbox import process_once
from .outbox import PublishedEvent

Handler = Callable[[PublishedEvent], None]

_CONSUMER = re.compile(r"^[a-z][a-z0-9_.-]{2,99}$")
_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,90}$")


@dataclass(frozen=True)
class Subscription:
    consumer: str       # a stable name: it is the key in the inbox, so never rename it casually
    prefix: str         # event types starting with it, e.g. "booking." or "booking.confirmed.v1"
    handler: Handler


_subscriptions: list[Subscription] = []


def subscribe(consumer: str, event_prefix: str, handler: Handler) -> None:
    """Register a handler. Call it once at start-up; a repeated consumer name is refused."""
    if not _CONSUMER.match(consumer) or not _PREFIX.match(event_prefix):
        raise ValueError("invalid consumer name or event prefix")
    if any(s.consumer == consumer for s in _subscriptions):
        raise ValueError(f"consumer {consumer!r} is already subscribed")
    _subscriptions.append(Subscription(consumer, event_prefix, handler))


def subscriptions() -> tuple[Subscription, ...]:
    return tuple(_subscriptions)


def clear_subscriptions() -> None:
    """For tests only."""
    _subscriptions.clear()


def dispatch(event: PublishedEvent) -> None:
    """Deliver one event to every matching subscriber. Raises if any handler fails."""
    ctx = build_context(event.tenant_id, event.cell_id)
    for sub in tuple(_subscriptions):
        if not event.event_type.startswith(sub.prefix):
            continue
        with tenant_transaction(ctx):    # a savepoint inside the relay's transaction
            process_once(sub.consumer, event.event_id, partial(sub.handler, event))
