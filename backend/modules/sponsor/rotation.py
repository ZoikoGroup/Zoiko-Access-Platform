"""Key rotation for payers' member numbers (DEV-216): the sealed number moves to the current key,
and its fingerprint is rebuilt, because fingerprints are made with the current key too."""
from __future__ import annotations

from foundation.cell_context import tenant_transaction
from foundation.crypto import CryptoError, decrypt
from foundation.rotation import RotationResult, rotate_model
from modules.trust import api as trust

from .models import PlanMember
from .services import member_context, member_key


def rotate(batch: int, dry_run: bool) -> RotationResult:
    total = RotationResult()
    for tenant in trust.list_tenants():
        ctx = trust.context_for(tenant["public_id"])
        scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
        with tenant_transaction(ctx):
            total += rotate_model(
                PlanMember, fields={"member_enc": lambda r: member_context(r.public_id)},
                tenant_id=ctx.tenant_id, scope=scope, batch=batch, dry_run=dry_run)
            if dry_run:
                continue
            for row in PlanMember.objects.filter(**scope).exclude(member_enc=""):
                try:
                    plain = decrypt(row.member_enc, tenant_id=ctx.tenant_id,
                                    context=member_context(row.public_id))
                except CryptoError:
                    continue                     # counted as unreadable above; left alone
                fresh = member_key(plain, ctx.tenant_id)
                if fresh != row.member_key:
                    row.member_key = fresh
                    row.save(update_fields=["member_key"])
    return total
