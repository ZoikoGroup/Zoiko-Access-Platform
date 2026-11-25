"""Closed message threads for retention (DEV-213, DEV-351).

The report counts closed conversations past the period. Erasing (only when switched on, and only
when a person asks) removes what was SAID: the subject, every message's text and every attached
file. The conversation's row, who and when, and the audit trail stay."""
from __future__ import annotations

import datetime as dt

from django.db import transaction

from foundation.cell_context import require_context

from .models import Message, MessageAttachment, Thread, ThreadStatus

ERASED_NAME = "erased"


def count_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Closed threads of the bound customer whose last message is older than the cut-off. An open
    conversation is never counted, however old."""
    ctx = require_context()
    return (Thread.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                                  status=ThreadStatus.CLOSED, last_message_at__lt=cutoff)
            .exclude(person_ref__in=held).exclude(subject_enc="").count())


def _due(cutoff: dt.datetime, held: frozenset[str], organization_ref: str):
    ctx = require_context()
    return (Thread.objects.filter(
        cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, organization_ref=organization_ref,
        status=ThreadStatus.CLOSED, last_message_at__lt=cutoff)
        .exclude(person_ref__in=held).exclude(subject_enc=""))


def due_for_erasure(cutoff: dt.datetime, held: frozenset[str], organization_ref: str) -> int:
    """How many closed conversations of THIS clinic are past the cut-off and still hold content."""
    return _due(cutoff, held, organization_ref).count()


def erase_older(cutoff: dt.datetime, held: frozenset[str], organization_ref: str) -> int:
    """Erase the content of this clinic's closed conversations past the cut-off, leaving out every
    patient on hold. Returns how many conversations were erased."""
    ctx = require_context()
    scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
    with transaction.atomic():
        threads = list(_due(cutoff, held, organization_ref).select_for_update())
        ids = [t.pk for t in threads]
        MessageAttachment.objects.filter(message__thread_id__in=ids, **scope).update(
            content_enc="", name=ERASED_NAME, digest="")
        Message.objects.filter(thread_id__in=ids, **scope).update(body_enc="")
        Thread.objects.filter(pk__in=ids, **scope).update(subject_enc="")
    return len(threads)
