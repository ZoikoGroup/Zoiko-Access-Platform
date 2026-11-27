"""Recurring family-access housekeeping, run by the worker (see foundation/periodic.py)."""
from __future__ import annotations

from foundation.cell_context import tenant_transaction

from .age_review import review_due
from .services import context_for, list_tenants

REVIEW_EVERY_SECONDS = 6 * 3600.0     # a few times a day: nobody waits long after their birthday


def review_all_tenants() -> int:
    """Suspend guardian access for children who have come of age, for every active customer."""
    total = 0
    for tenant in list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(context_for(tenant["public_id"])):
            total += review_due()
    return total
