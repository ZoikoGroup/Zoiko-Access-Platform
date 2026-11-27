"""Bookings for the retention report (DEV-213): counts only, nothing is deleted."""
from __future__ import annotations

import datetime as dt

from foundation.cell_context import require_context

from .models import Booking


def count_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Bookings of the bound customer whose appointment started before the cut-off."""
    ctx = require_context()
    return (Booking.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                                   slot__starts_at__lt=cutoff)
            .exclude(person_ref__in=held).count())
