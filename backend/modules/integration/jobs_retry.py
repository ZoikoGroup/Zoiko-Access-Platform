"""Hourly: try again FHIR messages held over a code, for every clinic (DEV-338)."""

from __future__ import annotations

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust

from .fhir import retry_code_held

RETRY_EVERY_SECONDS = 3600.0


def retry_all_tenants() -> int:
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active" or tenant["type"] == "platform":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            total += retry_code_held()
    return total
