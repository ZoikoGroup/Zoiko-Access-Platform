"""Key rotation for the sealed signing secrets: webhook endpoints and partner clients."""
from __future__ import annotations

from foundation.cell_context import tenant_transaction
from foundation.rotation import RotationResult, rotate_model
from modules.trust import api as trust

from .models import PartnerClient, WebhookEndpoint


def rotate(batch: int, dry_run: bool) -> RotationResult:
    total = RotationResult()
    for tenant in trust.list_tenants():
        ctx = trust.context_for(tenant["public_id"])
        scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
        with tenant_transaction(ctx):
            total += rotate_model(
                WebhookEndpoint,
                fields={"secret_enc": lambda r: f"integration:{r.public_id}:secret"},
                tenant_id=ctx.tenant_id, scope=scope, batch=batch, dry_run=dry_run)
            total += rotate_model(
                PartnerClient,
                fields={"secret_enc": lambda r: f"integration:{r.public_id}:client-secret"},
                tenant_id=ctx.tenant_id, scope=scope, batch=batch, dry_run=dry_run)
    return total
