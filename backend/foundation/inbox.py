"""Inbox: a consumer handles each event ONCE, even if the event is delivered many times.

process_once() records "consumer X handled event Y" in the SAME transaction as the handler's own
changes. Either both happen or neither does, so a crash can never leave an event half-handled,
and a duplicate delivery finds the record and does nothing.

Needs a bound tenant, because the inbox is tenant-scoped (row-level security applies).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

from django.db import IntegrityError, transaction

from .models import InboxMessage


def process_once(consumer: str, event_id: uuid.UUID, handler: Callable[[], None]) -> bool:
    """Run `handler` unless this consumer already handled this event.

    Returns True if the handler ran, False if it was a duplicate. If the handler raises, nothing
    is recorded and the exception propagates, so the event can be retried later.
    """
    with transaction.atomic():
        try:
            with transaction.atomic():  # savepoint: a duplicate must not abort the outer one
                InboxMessage.objects.create(consumer=consumer, event_id=event_id)
        except IntegrityError:
            return False
        handler()
        return True
