"""Who is calling the public API (DEV-393): an API key, never a person.

    Authorization: Bearer <key id>:<secret>

The key id names its customer (`pc.<customer>.<random>`, DEV-131), so the secret is checked inside
that customer only. Only an ACTIVE key works: Zoiko's identity, legal and security checks were
recorded and its secret issued (T-43). A suspended or revoked key, a wrong secret, or anything
malformed gets the same 401, and the answer never says which part was wrong.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from django.db import transaction

from foundation.cell_context import CellContext, tenant_transaction
from foundation.crypto import CryptoError, decrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.trust import api as trust

from .inbound import _KEY, _secret_context, scopes_of
from .models import ClientState, PartnerClient

MAX_TOKEN = 300


@dataclass(frozen=True)
class ApiCaller:
    context: CellContext
    client_ref: str
    label: str
    customer_ref: str
    customer_name: str
    scopes: tuple[str, ...]
    environment: str
    rate_per_minute: int


def _fail() -> ZoikoError:
    return ZoikoError(ErrorCode.UNAUTHENTICATED)


def authenticate(header: str | None, clock: Clock | None = None) -> ApiCaller:
    if not header or not header.startswith("Bearer ") or len(header) > MAX_TOKEN:
        raise _fail()
    key_id, _, secret = header[len("Bearer "):].strip().partition(":")
    found = _KEY.match(key_id)
    if not found or not secret:
        raise _fail()
    customer = found.group(1)
    try:
        ctx = trust.context_for(customer)
    except ZoikoError:
        raise _fail() from None
    with tenant_transaction(ctx), transaction.atomic():
        client = (PartnerClient.objects.select_for_update()
                  .filter(key_id=key_id, state=ClientState.ACTIVE, cell_id=ctx.cell_id,
                          tenant_id=ctx.tenant_id).first())
        if client is None or not client.secret_enc:
            raise _fail()
        try:
            real = decrypt(client.secret_enc, tenant_id=client.tenant_id,
                           context=_secret_context(client.public_id))
        except CryptoError:
            raise _fail() from None
        if not hmac.compare_digest(real.encode(), secret.encode()):
            raise _fail()
        client.last_used_at = utc_now(clock)
        client.save(update_fields=["last_used_at", "updated_at"])
        name = next((t["name"] for t in trust.list_tenants() if t["public_id"] == customer), "")
        return ApiCaller(ctx, client.public_id, client.label, customer, name,
                         tuple(scopes_of(client)), client.environment, client.rate_per_minute)


def require_scope(caller: ApiCaller, scope: str) -> None:
    if scope not in caller.scopes:
        raise ZoikoError(ErrorCode.POLICY_DENIED, fields=["scope"])
