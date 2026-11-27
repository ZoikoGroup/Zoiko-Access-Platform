"""Where queued email actually goes: a provider-neutral sender (GHE-16).

The mail queue (foundation/mail.py) decides WHAT to send and WHEN; a provider only delivers one
message. Three ship with the product, chosen by the MAIL_PROVIDER setting:

  smtp      the mail server in EMAIL_HOST (the default; what the product always did);
  http_api  a vendor's web API: one JSON POST with a bearer key (set MAIL_API_URL, MAIL_API_KEY);
  console   writes nothing anywhere real, for development: it only counts what would be sent.

A real vendor with a different request shape registers its own provider with `register_provider`.
A provider raises on failure (the queue retries and gives up as before) and may return the vendor's
own id for the message, so a later delivery receipt (`record_receipt`) can be matched to it.

Safety rules kept here:
  * an address or subject with a line break never reaches a provider (the queue already refuses it);
  * the web API is only called over https (unless MAIL_API_ALLOW_INSECURE, development only), never
    follows a redirect, and never sends the key anywhere but the configured address;
  * only the exception CLASS of a failure leaves this module, never its text (it can echo an
    address);
  * a delivery receipt is checked by an HMAC signature over the exact bytes before anything in it is
    read, and a receipt can only move a message FORWARD (a late "delivered" after "bounced" changes
    nothing).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction

from .errors import ErrorCode, ZoikoError
from .models import QueuedEmail
from .timeutil import Clock, utc_now

TIMEOUT_SECONDS = 10
MAX_RECEIPT_BYTES = 8192


@dataclass(frozen=True)
class MailMessage:
    to: str
    subject: str
    body: str
    from_email: str
    reference: str                       # our public id for the queued email


@dataclass(frozen=True)
class ProviderResult:
    provider_ref: str = ""               # the vendor's id for the message, if it gave one


class MailProvider:
    name = ""

    def send(self, message: MailMessage) -> ProviderResult:  # pragma: no cover - interface
        raise NotImplementedError


class SmtpProvider(MailProvider):
    name = "smtp"

    def send(self, message: MailMessage) -> ProviderResult:
        EmailMessage(subject=message.subject, body=message.body, to=[message.to],
                     from_email=message.from_email).send(fail_silently=False)
        return ProviderResult()


class ConsoleProvider(MailProvider):
    """Development only: nothing leaves the machine. Counts what would have been sent."""

    name = "console"
    sent: list[str] = []

    def send(self, message: MailMessage) -> ProviderResult:
        self.sent.append(message.reference)
        return ProviderResult(provider_ref=f"console-{message.reference}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class HttpApiProvider(MailProvider):
    """One JSON POST to the vendor. Any 2xx counts as accepted; the vendor's id is read from the
    answer's `id` field when it is there."""

    name = "http_api"

    def send(self, message: MailMessage) -> ProviderResult:
        url = getattr(settings, "MAIL_API_URL", "")
        key = getattr(settings, "MAIL_API_KEY", "")
        parts = urlsplit(url)
        insecure_ok = bool(getattr(settings, "MAIL_API_ALLOW_INSECURE", False))
        if not url or not key or parts.scheme not in ("https", "http") or not parts.hostname:
            raise MailProviderError("not_configured")
        if parts.scheme != "https" and not insecure_ok:
            raise MailProviderError("insecure_address")
        payload = json.dumps({
            "from": message.from_email, "to": message.to, "subject": message.subject,
            "text": message.body, "reference": message.reference}).encode()
        # The scheme was checked above: https, or http only when development allows it.
        request = urllib.request.Request(  # noqa: S310
            url, data=payload, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}",
            "Idempotency-Key": message.reference})
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as answer:
                status = answer.status
                raw = answer.read(MAX_RECEIPT_BYTES)
        except urllib.error.HTTPError as exc:            # 3xx (not followed), 4xx, 5xx
            raise MailProviderError(f"http_{exc.code}") from None
        if not 200 <= status < 300:
            raise MailProviderError(f"http_{status}")
        try:
            ref = str(json.loads(raw or b"{}").get("id", ""))[:64]
        except (ValueError, AttributeError):
            ref = ""
        return ProviderResult(provider_ref=ref)


class MailProviderError(RuntimeError):
    """A provider could not take the message. The text is a short code, never an address."""


_FACTORIES: dict[str, Callable[[], MailProvider]] = {
    "smtp": SmtpProvider, "console": ConsoleProvider, "http_api": HttpApiProvider}


def register_provider(name: str, factory: Callable[[], MailProvider]) -> None:
    """Add a provider (a real vendor's adapter, or a pretend one in a test)."""
    _FACTORIES[name] = factory


def get_provider() -> MailProvider:
    name = getattr(settings, "MAIL_PROVIDER", "smtp") or "smtp"
    factory = _FACTORIES.get(name)
    if factory is None:
        raise MailProviderError("unknown_provider")
    return factory()


# ------------------------------------------------------------------ delivery receipts
# How far a message has got. A receipt only ever moves it forward.
_RANK = {"accepted": 1, "deferred": 2, "delivered": 3, "bounced": 4, "complained": 5}


def receipt_signature(raw_body: bytes, secret: str) -> str:
    """HMAC-SHA256 over the exact bytes, hex."""
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def record_receipt(*, raw_body: bytes, signature: str, clock: Clock | None = None) -> int:
    """Check a vendor's delivery receipt and apply it. Returns the HTTP status to answer with: 403
    when the signature is wrong or no secret is set (nothing in the body is read), 400 for a body
    that is too large or not JSON, otherwise 200, including for a message we do not know (so
    nothing is learned by guessing ids). Body: {"id": "<provider id>", "status": "delivered"}."""
    secret = getattr(settings, "MAIL_WEBHOOK_SECRET", "")
    if not secret or not signature or not hmac.compare_digest(
            receipt_signature(raw_body, secret), signature):
        return 403
    if len(raw_body) > MAX_RECEIPT_BYTES:
        return 400
    try:
        data = json.loads(raw_body)
    except ValueError:
        return 400
    if not isinstance(data, dict):
        return 400
    ref, status = str(data.get("id", ""))[:64], str(data.get("status", ""))
    if not ref or status not in _RANK:
        return 200
    with transaction.atomic():
        row = QueuedEmail.objects.select_for_update().filter(provider_ref=ref).first()
        if row is None:
            return 200
        if _RANK[status] > _RANK.get(row.delivery_status, 0):
            row.delivery_status, row.delivery_updated_at = status, utc_now(clock)
            row.save(update_fields=["delivery_status", "delivery_updated_at", "updated_at"])
    return 200


def require_known_provider() -> None:
    """For the release check and start-up: refuse a setting that names no provider."""
    try:
        get_provider()
    except MailProviderError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["MAIL_PROVIDER"]) from None
