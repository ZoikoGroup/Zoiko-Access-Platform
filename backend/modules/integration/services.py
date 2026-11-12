"""Webhook endpoints and their deliveries (GHE-13 WF-09, WF-10).

Only a clinic administrator (R13) manages endpoints. An endpoint starts unverified and becomes
active only when its address answers a signed challenge. Events reach it through the same outbox
the rest of the system uses, as small reference-only messages (the receiver fetches details through
the normal, authorized API). Nothing here is exactly-once: a receiver must ignore a repeated
delivery id.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import re
import secrets
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context, tenant_transaction
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.dispatch import subscribe
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import PublishedEvent
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .delivery import (
    TIMEOUT_SECONDS,
    Transport,
    UnsafeTarget,
    check_url_shape,
    http_post,
    signature_header,
)
from .models import DeliveryState, EndpointState, WebhookDelivery, WebhookEndpoint

WRITE = "write:integration"
READ = "read:integration"
MANAGING_ROLES = frozenset({"R13"})
FAMILIES = ("booking.", "delegation.", "family.", "message.", "professional.", "slot.", "waitlist.")
MAX_ENDPOINTS = 10
MAX_TOPICS = 10
MAX_ATTEMPTS = 8
BASE_DELAY_SECONDS = 30
MAX_DELAY_SECONDS = 3600
PAUSE_AFTER_DEAD_LETTERS = 10
_TOPIC = re.compile(r"^[a-z][a-z0-9_.]{0,60}$")
_RETRYABLE_STATUS = frozenset({408, 425, 429})


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _secret_context(public_id: str) -> str:
    return f"integration:{public_id}:secret"


def _secret_of(ep: WebhookEndpoint) -> str:
    try:
        return decrypt(ep.secret_enc, tenant_id=ep.tenant_id, context=_secret_context(ep.public_id))
    except CryptoError as exc:
        raise ZoikoError(ErrorCode.INTERNAL_ERROR) from exc


def endpoint_dict(ep: WebhookEndpoint) -> dict[str, Any]:
    return {
        "public_id": ep.public_id, "label": ep.label, "url": ep.url, "topics": list(ep.topics),
        "key_id": ep.key_id, "state": ep.state, "created_at": ep.created_at.isoformat(),
        "verified_at": ep.verified_at.isoformat() if ep.verified_at else None,
        "failure_streak": ep.failure_streak,
    }


def delivery_dict(d: WebhookDelivery) -> dict[str, Any]:
    return {
        "public_id": d.public_id, "endpoint": d.endpoint.public_id, "event_type": d.event_type,
        "delivery_id": str(d.delivery_id), "state": d.state, "attempts": d.attempts,
        "next_attempt_at": d.next_attempt_at.isoformat(), "last_status": d.last_status,
        "last_error": d.last_error, "created_at": d.created_at.isoformat(),
        "owner": d.owner_ref, "policy_digest": d.policy_digest, "body_digest": d.body_digest,
        "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
    }


def _clean_label(value: str) -> str:
    text = " ".join((value or "").split())
    if not 2 <= len(text) <= 80 or any(ord(ch) < 32 for ch in text):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["label"])
    return text


def _clean_topics(topics: list[str]) -> list[str]:
    if not isinstance(topics, list) or not 1 <= len(topics) <= MAX_TOPICS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["topics"])
    clean = sorted({str(t) for t in topics})
    for topic in clean:
        if not _TOPIC.match(topic) or not topic.startswith(FAMILIES):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["topics"])
    return clean


def _new_secret() -> tuple[str, str]:
    return f"whk_{secrets.token_hex(4)}", f"whsec_{secrets.token_urlsafe(32)}"


def _managed(decision: policy.Decision, actor: str, organization_ref: str | None) -> None:
    policy.check_receipt(decision, actor_public_id=actor, operation=WRITE,
                         organization_public_id=organization_ref)
    if decision.role_code not in MANAGING_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


def _own(ref: str) -> WebhookEndpoint:
    found = WebhookEndpoint.objects.select_for_update().filter(public_id=ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def create_endpoint(
    *, label: str, url: str, topics: list[str], organization_ref: str | None,
    actor_public_id: str, decision: policy.Decision,
) -> dict[str, Any]:
    """Register an address. The signing secret is returned ONCE, here; it is stored sealed."""
    _managed(decision, actor_public_id, organization_ref)
    name = _clean_label(label)
    chosen = _clean_topics(topics)
    try:
        check_url_shape(url)
    except UnsafeTarget as exc:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["url"]) from exc
    with transaction.atomic():
        live = WebhookEndpoint.objects.filter(**_scope()).exclude(state=EndpointState.REVOKED)
        if live.count() >= MAX_ENDPOINTS:
            raise ZoikoError(ErrorCode.RATE_LIMITED)
        key_id, secret = _new_secret()
        row = WebhookEndpoint(label=name, url=url, topics=chosen, key_id=key_id, secret_enc="",
                              created_by_ref=actor_public_id, **_scope())
        row.secret_enc = encrypt(secret, tenant_id=row.tenant_id,
                                 context=_secret_context(row.public_id))
        row.save()
        record(actor_ref=actor_public_id, action="integration.endpoint_created",
               object_type="webhook_endpoint", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
        return {**endpoint_dict(row), "secret": secret}


def _send(
    ep: WebhookEndpoint, body: bytes, delivery_id: str, transport: Transport, now: dt.datetime
) -> tuple[int, bytes]:
    stamp = int(now.timestamp())
    headers = {
        "Content-Type": "application/json", "User-Agent": "Zoiko-Webhooks/1",
        "Zoiko-Delivery-Id": delivery_id,
        "Zoiko-Signature": signature_header(
            _secret_of(ep), ep.key_id, stamp, delivery_id, body),
    }
    return transport(ep.url, headers, body, TIMEOUT_SECONDS)


def verify_endpoint(
    *, endpoint_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None, transport: Transport | None = None, clock: Clock | None = None,
) -> dict[str, Any]:
    """Prove the address is the registrant's: send a signed challenge, which the receiver must
    answer with 2xx and JSON `{"challenge": "<same value>"}`. Only then is the endpoint active."""
    _managed(decision, actor_public_id, organization_ref)
    send = transport or http_post
    now = utc_now(clock)
    with transaction.atomic():
        ep = _own(endpoint_ref)
        if ep.state == EndpointState.REVOKED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        challenge = secrets.token_urlsafe(16)
        body = json.dumps({"type": "webhook.ping.v1", "challenge": challenge},
                          separators=(",", ":"), sort_keys=True).encode()
        try:
            status, answer = _send(ep, body, secrets.token_hex(16), send, now)
            ok = 200 <= status < 300 and json.loads(answer or b"{}").get("challenge") == challenge
        except (UnsafeTarget, OSError, ValueError, AttributeError):
            ok = False
        if not ok:
            record(actor_ref=actor_public_id, action="integration.verify_failed",
                   object_type="webhook_endpoint", object_ref=ep.public_id,
                   outcome=AuditOutcome.DENIED)
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["url"])
        if ep.state == EndpointState.PENDING:
            ep.state = EndpointState.ACTIVE
        ep.verified_at = now
        ep.entity_version += 1
        ep.save(update_fields=["state", "verified_at", "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="integration.verified",
               object_type="webhook_endpoint", object_ref=ep.public_id,
               outcome=AuditOutcome.ALLOWED)
        return endpoint_dict(ep)


def set_state(
    *, endpoint_ref: str, step: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """pause, resume (only an already-verified address) or revoke (final)."""
    _managed(decision, actor_public_id, organization_ref)
    with transaction.atomic():
        ep = _own(endpoint_ref)
        if ep.state == EndpointState.REVOKED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if step == "pause":
            target = EndpointState.PAUSED
        elif step == "resume":
            if ep.verified_at is None:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)   # verify first
            target = EndpointState.ACTIVE
        elif step == "revoke":
            target = EndpointState.REVOKED
        else:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if ep.state != target:
            ep.state = target
            if target == EndpointState.ACTIVE:
                ep.failure_streak = 0
            ep.entity_version += 1
            ep.save(update_fields=["state", "failure_streak", "entity_version", "updated_at"])
            record(actor_ref=actor_public_id, action=f"integration.{step}",
                   object_type="webhook_endpoint", object_ref=ep.public_id,
                   outcome=AuditOutcome.ALLOWED)
        return endpoint_dict(ep)


def rotate_secret(
    *, endpoint_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """A new secret and key id, shown once. The old secret stops working at once."""
    _managed(decision, actor_public_id, organization_ref)
    with transaction.atomic():
        ep = _own(endpoint_ref)
        if ep.state == EndpointState.REVOKED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        ep.key_id, secret = _new_secret()
        ep.secret_enc = encrypt(secret, tenant_id=ep.tenant_id,
                                context=_secret_context(ep.public_id))
        ep.entity_version += 1
        ep.save(update_fields=["key_id", "secret_enc", "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="integration.secret_rotated",
               object_type="webhook_endpoint", object_ref=ep.public_id,
               outcome=AuditOutcome.ALLOWED)
        return {**endpoint_dict(ep), "secret": secret}


def list_endpoints() -> list[dict[str, Any]]:
    rows = WebhookEndpoint.objects.filter(**_scope()).order_by("-created_at")
    return [endpoint_dict(r) for r in rows]


def gave_up_count() -> int:
    """How many deliveries have given up and wait for a person (a number only)."""
    return WebhookDelivery.objects.filter(state=DeliveryState.DEAD_LETTER, **_scope()).count()


def list_deliveries(state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    rows = WebhookDelivery.objects.select_related("endpoint").filter(**_scope())
    if state:
        if state not in DeliveryState.values:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["state"])
        rows = rows.filter(state=state)
    return [delivery_dict(d) for d in rows.order_by("-created_at")[: min(limit, 200)]]


def redrive(
    *, delivery_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None, clock: Clock | None = None,
) -> dict[str, Any]:
    """Put a dead-lettered delivery back in the queue (a person decided it is worth another go)."""
    _managed(decision, actor_public_id, organization_ref)
    with transaction.atomic():
        row = (WebhookDelivery.objects.select_for_update(of=("self",)).select_related("endpoint")
               .filter(public_id=delivery_ref, **_scope()).first())
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.state != DeliveryState.DEAD_LETTER or row.endpoint.state == EndpointState.REVOKED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.state = DeliveryState.PENDING
        row.attempts = 0
        row.last_error = ""
        row.next_attempt_at = utc_now(clock)
        row.entity_version += 1
        row.save(update_fields=["state", "attempts", "last_error", "next_attempt_at",
                                "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="integration.redrive",
               object_type="webhook_delivery", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
        return delivery_dict(row)


# ------------------------------------------------------------------ queueing events
def _body_of(event: PublishedEvent) -> str:
    return json.dumps({
        "id": str(event.event_id), "type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "aggregate": {"type": event.aggregate_type, "ref": event.aggregate_ref},
        "data": event.payload,
    }, separators=(",", ":"), sort_keys=True)


def enqueue(event: PublishedEvent) -> None:
    """Called inside the event's own tenant: queue one delivery per matching active endpoint."""
    body = _body_of(event)
    now = utc_now()
    for ep in WebhookEndpoint.objects.filter(state=EndpointState.ACTIVE, **_scope()):
        if not any(event.event_type.startswith(t) for t in ep.topics):
            continue
        WebhookDelivery.objects.get_or_create(
            endpoint=ep, event_id=event.event_id,
            defaults={"event_type": event.event_type, "body": body, "next_attempt_at": now,
                      "owner_ref": ep.created_by_ref, "policy_digest": policy.POLICY_DIGEST,
                      "body_digest": hashlib.sha256(body.encode()).hexdigest(), **_scope()})


def register_subscribers() -> None:
    for family in FAMILIES:
        try:
            subscribe(f"integration.webhooks.{family.rstrip('.')}", family, enqueue)
        except ValueError:      # already registered (the app registry was loaded twice in a test)
            pass


# ------------------------------------------------------------------ sending
def _delay(attempts: int) -> dt.timedelta:
    seconds = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * 2 ** max(attempts - 1, 0))
    return dt.timedelta(seconds=seconds * random.uniform(0.5, 1.5))  # noqa: S311


def _attempt(row: WebhookDelivery, transport: Transport, now: dt.datetime) -> None:
    ep = row.endpoint
    if ep.state == EndpointState.REVOKED:
        row.state, row.last_error = DeliveryState.DEAD_LETTER, "endpoint_revoked"
        return
    row.attempts += 1
    status: int | None = None
    error = ""
    try:
        status, _ = _send(ep, row.body.encode(), str(row.delivery_id), transport, now)
    except UnsafeTarget:
        error = "unsafe_target"
    except Exception as exc:    # noqa: BLE001 - only the class name is kept (messages can hold data)
        error = type(exc).__name__
    row.last_status, row.last_error = status, error
    if status is not None and 200 <= status < 300:
        row.state, row.delivered_at, row.last_error = DeliveryState.DELIVERED, now, ""
        ep.failure_streak = 0
    elif error == "unsafe_target" or (
        status is not None and 400 <= status < 500 and status not in _RETRYABLE_STATUS
    ):
        row.state = DeliveryState.DEAD_LETTER               # a clear "no": do not hammer it
    elif row.attempts >= MAX_ATTEMPTS:
        row.state = DeliveryState.DEAD_LETTER
    else:
        row.state, row.next_attempt_at = DeliveryState.RETRYING, now + _delay(row.attempts)
    if row.state == DeliveryState.DEAD_LETTER:
        ep.failure_streak += 1
        if ep.failure_streak >= PAUSE_AFTER_DEAD_LETTERS and ep.state == EndpointState.ACTIVE:
            ep.state = EndpointState.PAUSED                  # stop sending until a person looks
    ep.save(update_fields=["failure_streak", "state", "updated_at"])


def deliver_due(
    *, transport: Transport | None = None, limit: int = 50, clock: Clock | None = None
) -> dict[str, int]:
    """Send what is due in the CURRENT tenant. A paused endpoint's deliveries wait."""
    send = transport or http_post
    now = utc_now(clock)
    counts = {"delivered": 0, "retrying": 0, "dead_letter": 0}
    with transaction.atomic():
        due = list(
            WebhookDelivery.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("endpoint")
            .filter(state__in=(DeliveryState.PENDING, DeliveryState.RETRYING),
                    next_attempt_at__lte=now, **_scope())
            .exclude(endpoint__state=EndpointState.PAUSED)
            .order_by("next_attempt_at")[:limit])
        for row in due:
            _attempt(row, send, now)
            row.entity_version += 1
            row.save(update_fields=["state", "attempts", "next_attempt_at", "last_status",
                                    "last_error", "delivered_at", "entity_version", "updated_at"])
            if row.state in counts:
                counts[row.state] += 1
    return counts


def deliver_all_tenants(*, transport: Transport | None = None, limit: int = 50) -> dict[str, int]:
    """For the worker: every tenant in turn."""
    total = {"delivered": 0, "retrying": 0, "dead_letter": 0}
    for tenant in trust.list_tenants():
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            for key, value in deliver_due(transport=transport, limit=limit).items():
                total[key] += value
    return total
