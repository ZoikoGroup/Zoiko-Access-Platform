"""Bookings for the retention report (DEV-213) and, in an approved batch, anonymising (DEV-398).

Anonymising a booking detaches it from the patient: its patient reference becomes
`foundation.retention.ERASED`. The booking keeps only times, statuses and references to logins and
services, and its append-only step history stays whole. Nothing is deleted."""
from __future__ import annotations

import datetime as dt

from django.db.models import QuerySet

from foundation.cell_context import require_context
from foundation.retention import ERASED

from .models import Booking


def count_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Bookings of the bound customer whose appointment started before the cut-off."""
    return _older(cutoff, held).count()


def _older(cutoff: dt.datetime, held: frozenset[str]) -> QuerySet[Booking]:
    ctx = require_context()
    return (Booking.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                                   slot__starts_at__lt=cutoff)
            .exclude(person_ref__in=held).exclude(person_ref=ERASED))


def erase_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Detach every booking past the cut-off from its patient (never one under a lawful hold)."""
    ids = list(_older(cutoff, held).values_list("id", flat=True))
    Booking.objects.filter(id__in=ids).update(person_ref=ERASED, booked_by_ref=ERASED)
    return len(ids)
