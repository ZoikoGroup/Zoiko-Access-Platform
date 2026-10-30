"""Development-only login stub standing in for the real OIDC identity provider (DEV-010).

Header:  Authorization: Bearer dev:<tenant public id>:<issuer>:<subject>

There is NO signature, so anyone can claim to be anyone. It therefore works only when BOTH
DEBUG and DEV_AUTH_STUB are on, and never in production. The real version validates a signed
token and returns the same result type, so callers do not change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

from foundation.cell_context import CellContext, build_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError

from .auth import TOKEN_PREFIX, authenticate_token
from .models import Actor, Status, Tenant
from .services import active_roles

_PART = re.compile(r"^[A-Za-z0-9._@+-]{1,128}$")


@dataclass(frozen=True)
class Authenticated:
    context: CellContext
    actor_public_id: str
    actor_type: str
    tenant_name: str = ""
    tenant_type: str = ""
    roles: tuple[str, ...] = ()   # role codes held right now


def authenticate(authorization_header: str | None) -> Authenticated:
    """Signed session tokens (from password login) always work. The unsigned dev token only
    works when both DEBUG and DEV_AUTH_STUB are on."""
    signed = f"Bearer {TOKEN_PREFIX}."
    if authorization_header and authorization_header.startswith(signed):
        return authenticate_token(authorization_header[len("Bearer "):])
    if not (settings.DEBUG and getattr(settings, "DEV_AUTH_STUB", False)):
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    prefix = "Bearer dev:"
    if not authorization_header or not authorization_header.startswith(prefix):
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    parts = authorization_header[len(prefix):].split(":")
    if len(parts) != 3 or not all(_PART.match(p) for p in parts):
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    tenant_public_id, issuer, subject = parts

    tenant = Tenant.objects.filter(
        public_id=tenant_public_id, cell_id=settings.ZOIKO_CELL_ID, status=Status.ACTIVE
    ).first()
    if tenant is None:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    ctx = build_context(tenant.id)
    with tenant_transaction(ctx):
        actor = Actor.objects.filter(
            cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
            issuer=issuer, subject=subject, status=Status.ACTIVE,
        ).first()
        roles = tuple(r for r, _ in active_roles(actor.public_id)) if actor else ()
    if actor is None:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)  # same answer for every failure: no hints
    return Authenticated(
        ctx, actor.public_id, actor.actor_type, tenant.name, tenant.type, roles)
