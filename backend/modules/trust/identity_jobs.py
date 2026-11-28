"""Recurring identity housekeeping, run by the worker (see foundation/periodic.py)."""
from __future__ import annotations

from foundation.cell_context import tenant_transaction

from .identity_proof import refresh_expired
from .services import context_for, list_tenants

REFRESH_EVERY_SECONDS = 3600.0


def refresh_all_tenants() -> int:
    """Correct patients whose identity check has expired, for every active customer."""
    total = 0
    for tenant in list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(context_for(tenant["public_id"])):
            total += refresh_expired()
    return total
