"""The name a staff member, doctor or platform user is shown by.

Until now every one of them appeared as their login (an email address). A person can now choose a
display name for themselves. It is stored encrypted, set only by the person it belongs to, and is
NOT verified: nobody has checked it against a licence or an identity. Patients and guardians cannot
have one here (their names are patient details, see profile.py).
"""
from __future__ import annotations

from django.db import transaction

from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence.api import AuditOutcome, record

from .models import Actor, ActorName, ActorType

MAX_NAME = 80
_NOT_FOR = (ActorType.PATIENT, ActorType.PROXY)


def _context(actor_public_id: str) -> str:
    return f"actor:{actor_public_id}:display_name"


def _clean(value: object) -> str:
    name = " ".join(value.split()) if isinstance(value, str) else ""
    if not 2 <= len(name) <= MAX_NAME or any(ord(ch) < 32 for ch in name) or "@" in name:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["name"])
    return name


def set_display_name(actor_public_id: str, name: object) -> str:
    """The person sets their own name (empty text is not allowed: use clear_display_name).
    Needs a bound tenant and runs in its own transaction. Returns the cleaned name."""
    ctx = require_context()
    cleaned = _clean(name)
    with transaction.atomic():
        actor = Actor.objects.filter(
            public_id=actor_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
        if actor is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if actor.actor_type in _NOT_FOR:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        ActorName.objects.update_or_create(
            actor=actor, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
            defaults={"name_enc": encrypt(
                cleaned, tenant_id=ctx.tenant_id, context=_context(actor.public_id))})
        record(actor_ref=actor_public_id, action="actor.display_name", object_type="actor",
               object_ref=actor_public_id, outcome=AuditOutcome.ALLOWED)
    return cleaned


def clear_display_name(actor_public_id: str) -> None:
    """Go back to being shown by login. Repeating it is harmless."""
    ctx = require_context()
    with transaction.atomic():
        removed, _ = ActorName.objects.filter(
            actor__public_id=actor_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id
        ).delete()
        if removed:
            record(actor_ref=actor_public_id, action="actor.display_name_clear",
                   object_type="actor", object_ref=actor_public_id, outcome=AuditOutcome.ALLOWED)


def display_names() -> dict[str, str]:
    """public_id -> display name, for every person of the bound tenant who set one. A name that
    cannot be decrypted is left out (the person is then shown by login), never an error."""
    ctx = require_context()
    out: dict[str, str] = {}
    rows = ActorName.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).values_list(
        "actor__public_id", "name_enc")
    for public_id, token in rows:
        try:
            out[public_id] = decrypt(token, tenant_id=ctx.tenant_id, context=_context(public_id))
        except CryptoError:
            continue
    return out


def display_name_of(actor_public_id: str) -> str:
    """One person's display name: decrypts only that person's row, not everyone's."""
    ctx = require_context()
    token = (
        ActorName.objects.filter(
            actor__public_id=actor_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
        .values_list("name_enc", flat=True)
        .first()
    )
    if not token:
        return ""
    try:
        return decrypt(token, tenant_id=ctx.tenant_id, context=_context(actor_public_id))
    except CryptoError:
        return ""
