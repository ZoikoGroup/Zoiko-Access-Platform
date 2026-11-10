"""API clients, webhook subscriptions and signed delivery.

Keys look like  zk1.<tenant id>.<client id>.<secret>. The tenant id lets the request be bound to one
tenant before anything tenant-scoped is read; only a SHA-256 of the secret is stored. A client has
scopes and nothing else: no key opens any person's record by itself.

Webhook deliveries are made by a worker. An event becomes one delivery row per matching
subscription (once only, however often the event arrives); the worker signs and sends it outside any
transaction, and a delivery that keeps failing is given up (dead), then the subscription is switched
off after a run of dead deliveries. Receivers get the event's references, never patient content.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction

from foundation import crypto
from foundation.cell_context import CellContext, bind_context, require_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import PublishedEvent, backoff_seconds
from foundation.payloads import check_reference_payload
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.trust import api as trust

from .models import (
    ApiClient,
    ClientStatus,
    DeliveryState,
    Environment,
    SubscriptionStatus,
    WebhookDelivery,
    WebhookSubscription,
)
from .signing import sign_body

SCOPES = ("catalog:read", "webhooks:manage", "events:read")
# The only event families a partner can ever hear about. Everything else stays internal.
PUBLIC_EVENT_PREFIXES = ("booking.", "referral.", "connector.", "workflow.")
MAX_DELIVERY_ATTEMPTS = 8
DISABLE_AFTER_DEAD = 5
MAX_WEBHOOKS_PER_CLIENT = 10
SECRET_CONTEXT = "webhook.secret"  # noqa: S105 - a label for encryption, not a secret
_PREFIX = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
KEY_VERSION = "zk1"


@dataclass(frozen=True)
class AuthenticatedClient:
    client_ref: str
    tenant_ref: str
    context: CellContext
    environment: str
    scopes: tuple[str, ...]


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def client_dict(c: ApiClient) -> dict[str, Any]:
    return {
        "public_id": c.public_id, "name": c.name, "environment": c.environment,
        "scopes": list(c.scopes), "status": c.status,
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
    }


def webhook_dict(w: WebhookSubscription) -> dict[str, Any]:
    return {
        "public_id": w.public_id, "url": w.url, "event_prefixes": list(w.event_prefixes),
        "status": w.status, "dead_streak": w.dead_streak,
    }


def delivery_dict(d: WebhookDelivery) -> dict[str, Any]:
    return {
        "public_id": d.public_id, "event_id": d.event_id, "event_type": d.event_type,
        "state": d.state, "attempts": d.attempts, "last_status": d.last_status,
        "last_error": d.last_error,
        "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
    }


# --------------------------------------------------------------------------- clients


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_client(
    *, tenant_public_id: str, name: str, environment: str, scopes: list[str],
    created_by_ref: str, expires_at: dt.datetime | None = None,
) -> tuple[dict[str, Any], str]:
    """Make a client and return it with its API key. The key is shown ONCE: only a hash is kept."""
    if not name.strip() or len(name) > 100:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["name"])
    if environment not in Environment.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["environment"])
    if not scopes or any(s not in SCOPES for s in scopes) or len(set(scopes)) != len(scopes):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["scopes"])
    if not _NAME.match(created_by_ref):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["created_by"])
    secret = secrets.token_urlsafe(32)
    with tenant_transaction(trust.context_for(tenant_public_id)):
        client = ApiClient.objects.create(
            name=name.strip(), environment=environment, key_hash=_hash_secret(secret),
            scopes=list(scopes), expires_at=expires_at, created_by_ref=created_by_ref, **_scope())
        record(actor_ref=created_by_ref, action="developer.client_created",
               object_type="api_client", object_ref=client.public_id,
               outcome=AuditOutcome.ALLOWED)
        key = f"{KEY_VERSION}.{tenant_public_id}.{client.public_id}.{secret}"
        return client_dict(client), key


def revoke_client(client_ref: str, *, actor_ref: str) -> dict[str, Any]:
    """Stop a client at once. Its webhooks stop too (a revoked client's deliveries are not sent)."""
    with transaction.atomic():
        client = ApiClient.objects.select_for_update().filter(
            public_id=client_ref, **_scope()).first()
        if client is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if client.status != ClientStatus.REVOKED:
            client.status = ClientStatus.REVOKED
            client.save(update_fields=["status", "updated_at"])
            record(actor_ref=actor_ref, action="developer.client_revoked",
                   object_type="api_client", object_ref=client.public_id,
                   outcome=AuditOutcome.ALLOWED)
        return client_dict(client)


def authenticate_key(header: str | None, *, clock: Clock | None = None) -> AuthenticatedClient:
    """Check `Authorization: Bearer zk1.<tenant>.<client>.<secret>`. Every failure is the same
    UNAUTHENTICATED answer, so a caller learns nothing about which part was wrong."""
    fail = ZoikoError(ErrorCode.UNAUTHENTICATED)
    if not header or not header.startswith("Bearer "):
        raise fail
    parts = header[7:].strip().split(".")
    if len(parts) != 4 or parts[0] != KEY_VERSION:
        raise fail
    _, tenant_ref, client_ref, secret = parts
    active = {t["public_id"] for t in trust.list_tenants() if t["status"] == "active"}
    if tenant_ref not in active:
        raise fail
    ctx = trust.context_for(tenant_ref)
    now = utc_now(clock)
    with tenant_transaction(ctx):
        client = ApiClient.objects.filter(public_id=client_ref, **_scope()).first()
        if (client is None or client.status != ClientStatus.ACTIVE
                or not hmac.compare_digest(client.key_hash, _hash_secret(secret))
                or (client.expires_at is not None and client.expires_at <= now)):
            raise fail
        client.last_used_at = now
        client.save(update_fields=["last_used_at", "updated_at"])
        return AuthenticatedClient(
            client_ref=client.public_id, tenant_ref=tenant_ref, context=ctx,
            environment=client.environment, scopes=tuple(client.scopes))


def require_scope(client: AuthenticatedClient, scope: str) -> None:
    if scope not in client.scopes:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


# --------------------------------------------------------------------------- destinations


def check_destination(url: str, *, resolve: bool = True) -> None:
    """Refuse a webhook address that could reach our own network (server-side request forgery):
    https only, no credentials in the address, and every address it resolves to must be public.
    Development may allow plain http and private addresses explicitly."""
    bad = ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["url"])
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise bad from None
    allow_insecure = bool(getattr(settings, "WEBHOOK_ALLOW_INSECURE", False))
    if parts.scheme not in (("https", "http") if allow_insecure else ("https",)):
        raise bad
    if not host or parts.username or parts.password or len(url) > 500:
        raise bad
    if getattr(settings, "WEBHOOK_ALLOW_PRIVATE", False):
        return
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if not resolve:
            return
        try:
            infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        except OSError:
            raise bad from None
        addresses = [ipaddress.ip_address(str(i[4][0]).split("%")[0]) for i in infos]
    if not addresses or any(not a.is_global for a in addresses):
        raise bad


# --------------------------------------------------------------------------- subscriptions


def _prefixes(value: list[str]) -> list[str]:
    if not value or len(value) > 10 or any(
            not isinstance(p, str) or not _PREFIX.match(p) for p in value):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["events"])
    if any(not any(public.startswith(p) or p.startswith(public) for public in
                   PUBLIC_EVENT_PREFIXES) for p in value):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["events"])    # not a public family
    return value


def create_webhook(
    *, client_ref: str, url: str, event_prefixes: list[str]
) -> tuple[dict[str, Any], str]:
    """Subscribe a client to event families. Returns the subscription and its signing secret,
    shown once (it is stored encrypted)."""
    check_destination(url)
    prefixes = _prefixes(event_prefixes)
    ctx = require_context()
    secret = "whsec_" + secrets.token_urlsafe(32)  # noqa: S105
    with transaction.atomic():
        client = ApiClient.objects.filter(
            public_id=client_ref, status=ClientStatus.ACTIVE, **_scope()).first()
        if client is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if WebhookSubscription.objects.filter(client=client, **_scope()).count() >= (
                MAX_WEBHOOKS_PER_CLIENT):
            raise ZoikoError(ErrorCode.RATE_LIMITED)
        hook = WebhookSubscription.objects.create(
            client=client, url=url, event_prefixes=prefixes,
            secret_token=crypto.encrypt(secret, tenant_id=ctx.tenant_id, context=SECRET_CONTEXT),
            **_scope())
        record(actor_ref=client.public_id, action="developer.webhook_created",
               object_type="webhook", object_ref=hook.public_id, outcome=AuditOutcome.ALLOWED)
        return webhook_dict(hook), secret


def _own_webhook(client_ref: str, webhook_ref: str) -> WebhookSubscription:
    hook = WebhookSubscription.objects.select_for_update().select_related("client").filter(
        public_id=webhook_ref, client__public_id=client_ref, **_scope()).first()
    if hook is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)       # another client's webhook looks absent
    return hook


def set_webhook_status(client_ref: str, webhook_ref: str, status: str) -> dict[str, Any]:
    """A client pauses or resumes its own subscription (resuming also clears a failure streak)."""
    if status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAUSED):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["status"])
    with transaction.atomic():
        hook = _own_webhook(client_ref, webhook_ref)
        hook.status = status
        if status == SubscriptionStatus.ACTIVE:
            hook.dead_streak = 0
        hook.save(update_fields=["status", "dead_streak", "updated_at"])
        return webhook_dict(hook)


def list_webhooks(client_ref: str) -> list[dict[str, Any]]:
    rows = WebhookSubscription.objects.filter(client__public_id=client_ref, **_scope())
    return [webhook_dict(w) for w in rows.order_by("created_at")]


def list_deliveries(client_ref: str, webhook_ref: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = WebhookDelivery.objects.filter(
        subscription__public_id=webhook_ref, subscription__client__public_id=client_ref,
        **_scope()).order_by("-created_at")[:limit]
    return [delivery_dict(d) for d in rows]


# --------------------------------------------------------------------------- fan-out


def envelope_for(event: PublishedEvent) -> dict[str, Any]:
    """What a partner is told: the event's identity and references. Never patient content."""
    return check_reference_payload({
        "event_id": str(event.event_id), "event_type": event.event_type,
        "schema_version": event.schema_version, "occurred_at": event.occurred_at.isoformat(),
        "aggregate_type": event.aggregate_type, "aggregate_ref": event.aggregate_ref,
        "correlation_id": event.correlation_id, "cell_id": event.cell_id,
    })


def fan_out(event: PublishedEvent) -> int:
    """Queue the event for every active subscription that wants it. Runs in the event's tenant. An
    event already queued for a subscription is not queued twice."""
    if not event.event_type.startswith(PUBLIC_EVENT_PREFIXES):
        return 0
    body = envelope_for(event)
    queued = 0
    hooks = WebhookSubscription.objects.select_related("client").filter(
        status=SubscriptionStatus.ACTIVE, client__status=ClientStatus.ACTIVE, **_scope())
    for hook in hooks:
        if "events:read" not in hook.client.scopes:
            continue
        if not any(event.event_type.startswith(p) for p in hook.event_prefixes):
            continue
        try:
            with transaction.atomic():
                WebhookDelivery.objects.create(
                    subscription=hook, event_id=str(event.event_id),
                    event_type=event.event_type, payload=body, **_scope())
            queued += 1
        except IntegrityError:
            continue                                  # this event is already queued for it
    return queued


def send_test_event(client_ref: str, webhook_ref: str) -> dict[str, Any]:
    """Queue a synthetic `ping` for one subscription, so a developer can see signing and delivery
    work without waiting for a real event."""
    with transaction.atomic():
        hook = _own_webhook(client_ref, webhook_ref)
        ctx = require_context()
        event_id = secrets.token_hex(16)
        body = check_reference_payload({
            "event_id": event_id, "event_type": "developer.ping.v1", "schema_version": 1,
            "occurred_at": utc_now().isoformat(), "aggregate_type": "webhook",
            "aggregate_ref": hook.public_id, "correlation_id": event_id, "cell_id": ctx.cell_id})
        delivery = WebhookDelivery.objects.create(
            subscription=hook, event_id=event_id, event_type="developer.ping.v1", payload=body,
            **_scope())
        return delivery_dict(delivery)


# --------------------------------------------------------------------------- delivering

Sender = Callable[[str, dict[str, str], bytes], int]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect could send a delivery somewhere the address check never saw."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def http_sender(url: str, headers: dict[str, str], body: bytes) -> int:
    """Send one delivery. Returns the HTTP status; raises if the connection fails."""
    check_destination(url)                            # again, at send time: DNS may have changed
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=10) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _claim(now: dt.datetime, limit: int) -> list[WebhookDelivery]:
    with tenant_transaction():
        due = list(
            WebhookDelivery.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("subscription", "subscription__client")
            .filter(state=DeliveryState.PENDING, next_attempt_at__lte=now,
                    subscription__status=SubscriptionStatus.ACTIVE,
                    subscription__client__status=ClientStatus.ACTIVE, **_scope())
            .order_by("next_attempt_at")[:limit])
        for d in due:                                  # push the next try out before sending
            d.attempts += 1
            d.next_attempt_at = now + dt.timedelta(seconds=backoff_seconds(d.attempts))
            d.save(update_fields=["attempts", "next_attempt_at", "updated_at"])
    return due


def _finish(delivery_ref: str, status: int | None, error: str, now: dt.datetime) -> None:
    with tenant_transaction():
        d = WebhookDelivery.objects.select_for_update(of=("self",)).select_related(
            "subscription").get(public_id=delivery_ref, **_scope())
        if d.state != DeliveryState.PENDING:
            return
        d.last_status, d.last_error = status, error[:64]
        hook = d.subscription
        if status is not None and 200 <= status < 300:
            d.state, d.delivered_at = DeliveryState.DELIVERED, now
            hook.dead_streak = 0
        elif d.attempts >= MAX_DELIVERY_ATTEMPTS:
            d.state = DeliveryState.DEAD
            hook.dead_streak += 1
            if hook.dead_streak >= DISABLE_AFTER_DEAD:
                hook.status = SubscriptionStatus.DISABLED
        d.save()
        hook.save(update_fields=["dead_streak", "status", "updated_at"])


def deliver_due(
    *, sender: Sender | None = None, clock: Clock | None = None, limit: int = 20
) -> int:
    """One worker pass for the bound tenant. Returns how many deliveries were attempted."""
    send = sender or http_sender
    now = utc_now(clock)
    claimed = _claim(now, limit)
    for d in claimed:
        hook = d.subscription
        ctx = require_context()
        status: int | None = None
        error = ""
        secret = crypto.decrypt(
            hook.secret_token, tenant_id=ctx.tenant_id, context=SECRET_CONTEXT)
        body = json.dumps(d.payload, sort_keys=True, separators=(",", ":")).encode()
        stamp = int(utc_now(clock).timestamp())
        headers = {
            "Content-Type": "application/json", "User-Agent": "Zoiko-Webhooks/1",
            "X-Zoiko-Event-Id": d.event_id, "X-Zoiko-Event-Type": d.event_type,
            "X-Zoiko-Timestamp": str(stamp), "Idempotency-Key": d.event_id,
            "X-Zoiko-Signature": sign_body(secret, stamp, body),
        }
        try:
            status = send(hook.url, headers, body)
        except Exception as exc:  # noqa: BLE001 - any failure to send means "try later"
            error = type(exc).__name__
        _finish(d.public_id, status, error, utc_now(clock))
    return len(claimed)


def redrive_delivery(client_ref: str, delivery_ref: str) -> dict[str, Any]:
    """A client asks for a given-up delivery to be tried again."""
    with transaction.atomic():
        d = WebhookDelivery.objects.select_for_update(of=("self",)).filter(
            public_id=delivery_ref, subscription__client__public_id=client_ref,
            **_scope()).first()
        if d is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if d.state != DeliveryState.DEAD:
            raise ZoikoError(ErrorCode.UNSAFE_REPLAY)
        d.state, d.attempts, d.next_attempt_at = DeliveryState.PENDING, 0, utc_now()
        d.save()
        return delivery_dict(d)


def deliver_all_tenants() -> int:
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        with bind_context(trust.context_for(tenant["public_id"])):    # deliver_due opens its own
            total += deliver_due()
    return total
