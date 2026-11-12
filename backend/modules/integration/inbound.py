"""Partners sending to us: credentials, and checking one signed message (GHE-13 WF-04, WF-08).

`receive()` is the whole of WF-08, in this order, and stops at the first failure:

  1  body not over 4 KB                              -> 413
  2  headers well formed                             -> 401 (one answer for every identity failure)
  3  timestamp within 5 minutes of now               -> 401
  4  the key names a live partner of a live customer -> 401
  5  signature over `time.delivery id.exact body`    -> 401 (constant-time comparison)
  6  the partner is within its per-minute budget     -> 429
  7  a delivery id seen before:
       same fingerprint  -> 202 "duplicate" (safe acceptance, nothing happens twice)
       different one     -> 409, nothing stored
  8  the body is JSON of the allowed shape and kind  -> 422, the message is HELD (fingerprint only)
  9  accepted: stored                                -> 202

Nothing here changes any booking, patient or clinical record: the specification says a verified
message then goes to the workflow engine for semantic checks, and that does not exist (DEV-131).
Signature proves transport origin only, not that a named licensed person attested anything.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context, tenant_transaction
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.payloads import PayloadError, check_reference_payload
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .delivery import sign
from .models import ClientState, InboundEvent, InboundState, PartnerClient

WRITE = "write:integration"
MANAGING_ROLES = frozenset({"R13"})
MAX_BODY = 4096
MAX_SKEW = dt.timedelta(minutes=5)
MAX_CLIENTS = 10
MAX_TYPES = 10
_KEY = re.compile(r"^pc\.([A-Za-z0-9_-]{8,40})\.([0-9a-f]{8})$")
_DELIVERY = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_SIGNATURE = re.compile(r"^t=(\d{9,12}),kid=([A-Za-z0-9._-]{10,80}),v1=([0-9a-f]{64})$")
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.]{2,90}\.v[0-9]{1,3}$")
_PREFIX = re.compile(r"^partner\.[a-z][a-z0-9_.]{0,60}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _secret_context(public_id: str) -> str:
    return f"integration:{public_id}:client-secret"


def client_dict(c: PartnerClient) -> dict[str, Any]:
    return {
        "public_id": c.public_id, "label": c.label, "key_id": c.key_id,
        "allowed_types": list(c.allowed_types), "state": c.state,
        "rate_per_minute": c.rate_per_minute, "created_at": c.created_at.isoformat(),
        "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
    }


def event_dict(e: InboundEvent) -> dict[str, Any]:
    return {
        "public_id": e.public_id, "client": e.client.public_id, "client_label": e.client.label,
        "delivery_id": e.delivery_id, "event_type": e.event_type, "state": e.state,
        "reason": e.reason, "body_digest": e.body_digest, "created_at": e.created_at.isoformat(),
    }


# ------------------------------------------------------------------ the partner's credential
def _managed(decision: policy.Decision, actor: str, organization_ref: str | None) -> None:
    policy.check_receipt(decision, actor_public_id=actor, operation=WRITE,
                         organization_public_id=organization_ref)
    if decision.role_code not in MANAGING_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


def _own(ref: str) -> PartnerClient:
    found = PartnerClient.objects.select_for_update().filter(public_id=ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def _clean_types(types: Any) -> list[str]:
    if not isinstance(types, list) or not 1 <= len(types) <= MAX_TYPES:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["allowed_types"])
    clean = sorted({str(t) for t in types})
    if any(not _PREFIX.match(t) for t in clean):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["allowed_types"])
    return clean


def create_client(
    *, label: str, allowed_types: Any, organization_ref: str | None, actor_public_id: str,
    decision: policy.Decision,
) -> dict[str, Any]:
    """Register a partner. The signing secret is shown ONCE here and stored sealed."""
    _managed(decision, actor_public_id, organization_ref)
    name = " ".join((label or "").split())
    if not 2 <= len(name) <= 80 or any(ord(ch) < 32 for ch in name):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["label"])
    chosen = _clean_types(allowed_types)
    ctx = require_context()
    tenant_public = trust.tenant_public_id(ctx.tenant_id)
    with transaction.atomic():
        live = PartnerClient.objects.filter(**_scope()).exclude(state=ClientState.REVOKED)
        if live.count() >= MAX_CLIENTS:
            raise ZoikoError(ErrorCode.RATE_LIMITED)
        secret = f"pcsec_{secrets.token_urlsafe(32)}"
        row = PartnerClient(label=name, key_id=f"pc.{tenant_public}.{secrets.token_hex(4)}",
                            secret_enc="", allowed_types=chosen, created_by_ref=actor_public_id,
                            **_scope())
        row.secret_enc = encrypt(secret, tenant_id=row.tenant_id,
                                 context=_secret_context(row.public_id))
        row.save()
        record(actor_ref=actor_public_id, action="integration.partner_created",
               object_type="partner_client", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
        return {**client_dict(row), "secret": secret}


def set_client_state(
    *, client_ref: str, step: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """suspend (denies at once, keeps the evidence), resume, or revoke (final)."""
    _managed(decision, actor_public_id, organization_ref)
    targets = {"suspend": ClientState.SUSPENDED, "resume": ClientState.ACTIVE,
               "revoke": ClientState.REVOKED}
    if step not in targets:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    with transaction.atomic():
        row = _own(client_ref)
        if row.state == ClientState.REVOKED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if row.state != targets[step]:
            row.state = targets[step]
            row.entity_version += 1
            row.save(update_fields=["state", "entity_version", "updated_at"])
            record(actor_ref=actor_public_id, action=f"integration.partner_{step}",
                   object_type="partner_client", object_ref=row.public_id,
                   outcome=AuditOutcome.ALLOWED)
        return client_dict(row)


def rotate_client_secret(
    *, client_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """A new secret, shown once; the old one stops working at once (no overlap window yet)."""
    _managed(decision, actor_public_id, organization_ref)
    with transaction.atomic():
        row = _own(client_ref)
        if row.state == ClientState.REVOKED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        secret = f"pcsec_{secrets.token_urlsafe(32)}"
        row.secret_enc = encrypt(secret, tenant_id=row.tenant_id,
                                 context=_secret_context(row.public_id))
        row.entity_version += 1
        row.save(update_fields=["secret_enc", "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="integration.partner_secret_rotated",
               object_type="partner_client", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
        return {**client_dict(row), "secret": secret}


def list_clients() -> list[dict[str, Any]]:
    rows = PartnerClient.objects.filter(**_scope()).order_by("-created_at")
    return [client_dict(r) for r in rows]


def list_events(state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    rows = InboundEvent.objects.select_related("client").filter(**_scope())
    if state:
        if state not in InboundState.values:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["state"])
        rows = rows.filter(state=state)
    return [event_dict(e) for e in rows.order_by("-created_at")[: min(limit, 200)]]


# ------------------------------------------------------------------ one signed message arriving
Answer = tuple[int, dict[str, Any], dict[str, str]]     # status, body, extra headers


def _reply(status: int, **body: Any) -> Answer:
    return status, body, {}


def receive(*, raw_body: bytes, signature: str, delivery_id: str, clock: Clock | None = None
            ) -> Answer:
    """Check and store one partner message. Raises UNAUTHENTICATED for every identity failure."""
    now = utc_now(clock)
    if len(raw_body) > MAX_BODY:
        return _reply(413, error="body_too_large")
    parsed = _SIGNATURE.match(signature or "")
    key = _KEY.match(parsed.group(2)) if parsed else None
    if not parsed or not key or not _DELIVERY.match(delivery_id or ""):
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    stamp, key_id, given = int(parsed.group(1)), parsed.group(2), parsed.group(3)
    if abs(now - dt.datetime.fromtimestamp(stamp, dt.timezone.utc)) > MAX_SKEW:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    try:
        ctx = trust.context_for(key.group(1))
    except ZoikoError:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED) from None
    with tenant_transaction(ctx):
        client = (PartnerClient.objects.select_for_update()
                  .filter(key_id=key_id, state=ClientState.ACTIVE, **_scope()).first())
        if client is None:
            raise ZoikoError(ErrorCode.UNAUTHENTICATED)
        try:
            secret = decrypt(client.secret_enc, tenant_id=client.tenant_id,
                             context=_secret_context(client.public_id))
        except CryptoError:
            raise ZoikoError(ErrorCode.UNAUTHENTICATED) from None
        if not hmac.compare_digest(sign(secret, stamp, delivery_id, raw_body), given):
            raise ZoikoError(ErrorCode.UNAUTHENTICATED)
        return _accept(client, raw_body, delivery_id, now)


def _accept(client: PartnerClient, raw_body: bytes, delivery_id: str, now: dt.datetime) -> Answer:
    digest = hashlib.sha256(raw_body).hexdigest()
    recent = InboundEvent.objects.filter(
        client=client, created_at__gte=now - dt.timedelta(minutes=1), **_scope()).count()
    if recent >= client.rate_per_minute:
        return 429, {"error": "rate_limited"}, {"Retry-After": "60"}
    seen = InboundEvent.objects.filter(client=client, delivery_id=delivery_id, **_scope()).first()
    if seen is not None:
        if seen.body_digest == digest:
            return _reply(202, received=True, duplicate=True, id=seen.public_id)
        return _reply(409, error="delivery_id_reused_with_different_content")
    client.last_used_at = now
    client.save(update_fields=["last_used_at", "updated_at"])
    event_type, reason = "", ""
    try:
        data = json.loads(raw_body)
        if not isinstance(data, dict) or set(data) - {"type", "data"}:
            raise ValueError
        event_type = str(data.get("type", ""))
        if not _EVENT_TYPE.match(event_type):
            raise ValueError
        check_reference_payload(data.get("data") or {})
        if not any(event_type.startswith(p) for p in client.allowed_types):
            reason = "unsupported_event"
    except (ValueError, TypeError, PayloadError):
        reason = "schema"
    try:
        with transaction.atomic():
            row = InboundEvent.objects.create(
                client=client, delivery_id=delivery_id, event_type=event_type[:100],
                body_digest=digest, body="" if reason else raw_body.decode("utf-8", "replace"),
                state=InboundState.QUARANTINED if reason else InboundState.RECEIVED,
                reason=reason, **_scope())
    except IntegrityError:          # two copies arrived at once: the other one won
        return _reply(202, received=True, duplicate=True)
    if reason:
        return _reply(422, error=reason, held=True, id=row.public_id)
    return _reply(202, received=True, duplicate=False, id=row.public_id)
