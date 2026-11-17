"""Who is under a lawful hold, for the retention report (DEV-213, with DEV-192).

A hold is recorded on one data request. For retention it is taken broadly: while any request of a
patient carries a hold, in whatever state, none of that patient's records count as due. Keeping
too much is the safe mistake; the lead may narrow this when retention is decided."""
from __future__ import annotations

from foundation.cell_context import require_context

from .models import RightsCase


def persons_on_hold() -> frozenset[str]:
    ctx = require_context()
    return frozenset(RightsCase.objects.filter(
        cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, lawful_hold=True
    ).values_list("person_ref", flat=True))
