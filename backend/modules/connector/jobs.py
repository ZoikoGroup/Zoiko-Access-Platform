"""The connector worker pass (a recurring job, see foundation/periodic.py).

Each pass, for every active customer: recover calls that were left half-done, send what is due, and
ask partners about what is still unsettled.
"""
from __future__ import annotations

from foundation.cell_context import bind_context, tenant_transaction
from modules.trust import api as trust

from .services import reconcile_due, recover_stuck, send_due

RUN_EVERY_SECONDS = 5.0


def run_all_tenants() -> int:
    handled = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        ctx = trust.context_for(tenant["public_id"])
        with tenant_transaction(ctx):
            handled += recover_stuck()
        # send_due and reconcile_due manage their own short transactions: the partner is called
        # between them, never inside one. They need the tenant bound, not a transaction around.
        with bind_context(ctx):
            handled += send_due()
            handled += reconcile_due()
    return handled
