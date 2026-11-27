"""Recurring waiting-list jobs, run by the worker (see foundation/periodic.py)."""
from __future__ import annotations

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust

from .services import expire_old_offers

EXPIRE_EVERY_SECONDS = 600.0


def expire_all_tenants() -> int:
    """Return unused offers to the list, for every active customer."""
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            total += expire_old_offers()
    return total
