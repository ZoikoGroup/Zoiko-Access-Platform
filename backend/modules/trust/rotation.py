"""Key rotation for the encrypted values the trust module stores (patient details, staff names)."""
from __future__ import annotations

from foundation.cell_context import tenant_transaction
from foundation.rotation import RotationResult, rotate_model

from .models import ActorName, PersonProfile
from .services import context_for, list_tenants

_PROFILE = {
    "legal_name_enc": lambda r: f"person:{r.person.public_id}:legal_name",
    "birth_date_enc": lambda r: f"person:{r.person.public_id}:birth_date",
    "phone_enc": lambda r: f"person:{r.person.public_id}:phone",
}
_NAME = {"name_enc": lambda r: f"actor:{r.actor.public_id}:display_name"}


def rotate(batch: int, dry_run: bool) -> RotationResult:
    total = RotationResult()
    for tenant in list_tenants():
        ctx = context_for(tenant["public_id"])
        scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
        with tenant_transaction(ctx):
            total += rotate_model(PersonProfile, fields=_PROFILE, tenant_id=ctx.tenant_id,
                                  scope=scope, batch=batch, dry_run=dry_run,
                                  select_related=("person",))
            total += rotate_model(ActorName, fields=_NAME, tenant_id=ctx.tenant_id, scope=scope,
                                  batch=batch, dry_run=dry_run, select_related=("actor",))
    return total
