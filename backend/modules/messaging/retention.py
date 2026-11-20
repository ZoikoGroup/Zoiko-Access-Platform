"""Closed message threads for the retention report (DEV-213): counts only, nothing is deleted."""
from __future__ import annotations

import datetime as dt

from foundation.cell_context import require_context

from .models import Thread, ThreadStatus


def count_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Closed threads of the bound customer whose last message is older than the cut-off. An open
    conversation is never counted, however old."""
    ctx = require_context()
    return (Thread.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                                  status=ThreadStatus.CLOSED, last_message_at__lt=cutoff)
            .exclude(person_ref__in=held).count())
