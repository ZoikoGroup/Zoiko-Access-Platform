"""Closed message threads for the retention report (DEV-213) and, in an approved batch,
anonymising (DEV-398): the subject and every message are replaced by a short note that they were
removed, and the thread is detached from the patient. Rows stay; nothing is deleted."""
from __future__ import annotations

import datetime as dt

from django.db.models import QuerySet

from foundation.cell_context import require_context
from foundation.retention import ERASED

from .models import Message, Thread, ThreadStatus

REMOVED = "Removed after the retention period."


def count_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Closed threads of the bound customer whose last message is older than the cut-off. An open
    conversation is never counted, however old."""
    return _older(cutoff, held).count()


def _older(cutoff: dt.datetime, held: frozenset[str]) -> QuerySet[Thread]:
    ctx = require_context()
    return (Thread.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                                  status=ThreadStatus.CLOSED, last_message_at__lt=cutoff)
            .exclude(person_ref__in=held).exclude(person_ref=ERASED))


def erase_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Remove what was written in every closed thread past the cut-off (never under a hold)."""
    from .services import _seal

    done = 0
    for thread in _older(cutoff, held):
        thread.subject_enc = _seal(REMOVED, "thread", thread.public_id, "subject")
        thread.person_ref = ERASED
        thread.save(update_fields=["subject_enc", "person_ref", "updated_at"])
        for message in Message.objects.filter(thread=thread):
            message.body_enc = _seal(REMOVED, "message", message.public_id, "body")
            message.save(update_fields=["body_enc", "updated_at"])
        done += 1
    return done
