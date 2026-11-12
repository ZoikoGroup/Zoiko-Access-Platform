"""Key rotation for message subjects and bodies."""
from __future__ import annotations

from foundation.cell_context import tenant_transaction
from foundation.rotation import RotationResult, rotate_model
from modules.trust import api as trust

from .models import Message, Thread


def rotate(batch: int, dry_run: bool) -> RotationResult:
    total = RotationResult()
    for tenant in trust.list_tenants():
        ctx = trust.context_for(tenant["public_id"])
        scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
        with tenant_transaction(ctx):
            total += rotate_model(
                Thread, fields={"subject_enc": lambda r: f"thread:{r.public_id}:subject"},
                tenant_id=ctx.tenant_id, scope=scope, batch=batch, dry_run=dry_run)
            total += rotate_model(
                Message, fields={"body_enc": lambda r: f"message:{r.public_id}:body"},
                tenant_id=ctx.tenant_id, scope=scope, batch=batch, dry_run=dry_run)
    return total
