"""The audit log for the retention report (DEV-398): counted only. Entries are a tamper-evident
chain, so none is removed or changed here; removing old ones safely needs a sealed checkpoint of the
chain first, which is not built."""
from __future__ import annotations

import datetime as dt

from foundation.cell_context import require_context

from .models import AuditEvent


def count_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    ctx = require_context()
    return AuditEvent.objects.filter(tenant_id=ctx.tenant_id, occurred_at__lt=cutoff).count()
