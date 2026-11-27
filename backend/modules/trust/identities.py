"""The outside sign-ins linked to a person's own login (DEV-356): seeing them, unlinking one and
allowing it again.

Unlinking keeps the row, marked unlinked, because a first sign-in with a provider links itself by
verified email: removing the row would simply link it again next time. An unlinked sign-in is
refused until the person allows it again here. Both need the password.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import Credential, ExternalIdentity
from .sso import providers


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _dict(row: ExternalIdentity, names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": row.public_id, "provider": row.provider,
        "provider_name": names.get(row.provider, row.provider), "email": row.email,
        "linked_at": row.linked_at.isoformat(),
        "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
        "unlinked_at": row.unlinked_at.isoformat() if row.unlinked_at else None,
    }


def mine(actor_public_id: str) -> list[dict[str, Any]]:
    names = {p.key: p.name for p in providers().values()}
    rows = ExternalIdentity.objects.filter(
        actor__public_id=actor_public_id, **_scope()).order_by("linked_at")
    return [_dict(r, names) for r in rows]


def change(
    actor_public_id: str, identity_ref: str, step: str, *, password: Any,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """`unlink` stops this outside sign-in working; `allow` lets it work again."""
    from .auth import verify_password

    if step not in ("unlink", "allow"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    with transaction.atomic():
        cred = Credential.objects.filter(actor__public_id=actor_public_id, **_scope()).first()
        row = (ExternalIdentity.objects.select_for_update()
               .filter(public_id=identity_ref, actor__public_id=actor_public_id, **_scope())
               .first())
        if cred is None or row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)              # someone else's: not found
        if not isinstance(password, str) or not verify_password(cred.password_hash, password):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["password"])
        wanted_unlinked = step == "unlink"
        if (row.unlinked_at is not None) != wanted_unlinked:
            row.unlinked_at = utc_now(clock) if wanted_unlinked else None
            row.entity_version += 1
            row.save(update_fields=["unlinked_at", "entity_version", "updated_at"])
            record(actor_ref=actor_public_id, action=f"auth.sso_{step}", object_type="actor",
                   object_ref=actor_public_id, outcome=AuditOutcome.ALLOWED,
                   reason_code=row.provider)
        names = {p.key: p.name for p in providers().values()}
        return _dict(row, names)
