"""Key rotation for the sealed notes on data-rights requests."""
from __future__ import annotations

from foundation.cell_context import tenant_transaction
from foundation.rotation import RotationResult, rotate_model
from modules.trust import api as trust

from .models import RightsCase


def rotate(batch: int, dry_run: bool) -> RotationResult:
    total = RotationResult()
    for tenant in trust.list_tenants():
        ctx = trust.context_for(tenant["public_id"])
        scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
        with tenant_transaction(ctx):
            total += rotate_model(
                RightsCase, fields={"note_enc": lambda r: f"rights:{r.public_id}:note"},
                tenant_id=ctx.tenant_id, scope=scope, batch=batch, dry_run=dry_run)
    return total
