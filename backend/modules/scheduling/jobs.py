"""Recurring scheduling jobs, run by the worker (see foundation/periodic.py)."""
from __future__ import annotations

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust

from .services import send_due_reminders

REMIND_EVERY_SECONDS = 300.0


def remind_all_tenants() -> int:
    """Send the appointment reminders that are due, for every active customer."""
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            total += send_due_reminders()
    return total
