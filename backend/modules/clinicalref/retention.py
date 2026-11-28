"""Clinical references for the retention report (DEV-213, DEV-220): counts only."""
from __future__ import annotations

import datetime as dt

from foundation.cell_context import require_context

from .models import ClinicalReference


def count_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Pointers of the bound customer to records about a day before the cut-off."""
    ctx = require_context()
    return (ClinicalReference.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                                             refers_to__lt=cutoff.date())
            .exclude(person_ref__in=held).count())
