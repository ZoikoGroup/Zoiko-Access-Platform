"""Sending email through SendGrid's web API (DEV-350, LEAD DECISION: SendGrid).

A Django email backend, so the queue, retries, masking and wiping in foundation/mail.py stay
exactly as they are; only the last step (handing the message over) changes. HTTPS to the API, not
SMTP, so it works from Cloud Run without opening mail ports. The API key comes from the
environment (`SENDGRID_API_KEY`) and is never logged; a failure raises, so the queue tries again.
`SENDGRID_API_HOST` lets the EU data-residency host be used if the account is set up that way.
"""

from __future__ import annotations

import json
import urllib.request
from email.utils import parseaddr
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

DEFAULT_HOST = "https://api.sendgrid.com"


class SendGridError(RuntimeError):
    pass


def _address(text: str) -> dict[str, str]:
    name, email = parseaddr(text)
    return {"email": email, **({"name": name} if name else {})}


class SendGridBackend(BaseEmailBackend):
    def send_messages(self, email_messages: Any) -> int:
        key = getattr(settings, "SENDGRID_API_KEY", "")
        if not key:
            if self.fail_silently:
                return 0
            raise SendGridError("SENDGRID_API_KEY is not set")
        host = (getattr(settings, "SENDGRID_API_HOST", "") or DEFAULT_HOST).rstrip("/")
        if not host.startswith("https://"):
            raise SendGridError("SENDGRID_API_HOST must be https")
        # Only the provider's own hosts (DEV-384): a mistyped setting must not send mail elsewhere.
        allowed = tuple(getattr(settings, "EGRESS_PROVIDER_HOSTS", ()))
        if (urlsplit(host).hostname or "").lower() not in allowed:
            raise SendGridError("SENDGRID_API_HOST is not an allowed provider host")
        sent = 0
        for message in email_messages:
            payload = {
                "personalizations": [{"to": [_address(a) for a in message.to]}],
                "from": _address(message.from_email or settings.DEFAULT_FROM_EMAIL),
                "subject": message.subject,
                "content": [{"type": "text/plain", "value": message.body}],
                # No open or click tracking: they would add tracking links to clinical mail.
                "tracking_settings": {"click_tracking": {"enable": False},
                                      "open_tracking": {"enable": False}},
            }
            ref = (message.extra_headers or {}).get("X-Zoiko-Ref")
            if ref:  # comes back in the delivery receipt (DEV-354); an id, nothing personal
                payload["custom_args"] = {"zoiko_ref": str(ref)[:32]}
            request = urllib.request.Request(  # noqa: S310 - host checked to be https above
                f"{host}/v3/mail/send", data=json.dumps(payload).encode(), method="POST",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=10) as answer:  # noqa: S310
                    if answer.status >= 300:
                        raise SendGridError(f"sendgrid answered {answer.status}")
            except Exception:
                if not self.fail_silently:
                    raise
                continue
            sent += 1
        return sent



# ------------------------------------------------------------------ delivery receipts (DEV-354)
SIGNATURE_HEADER = "X-Twilio-Email-Event-Webhook-Signature"
TIMESTAMP_HEADER = "X-Twilio-Email-Event-Webhook-Timestamp"
MAX_SKEW_SECONDS = 600
EVENT_STATUS = {"delivered": "delivered", "bounce": "bounced", "dropped": "dropped",
                "deferred": "deferred"}


def verify_event_signature(body: bytes, signature: str, timestamp: str, now: float) -> bool:
    """SendGrid signs its event webhook with ECDSA (P-256, SHA-256) over timestamp + body; the
    public key comes from the SendGrid account (`SENDGRID_WEBHOOK_PUBLIC_KEY`, base64 DER). No key
    set, a stale timestamp or a bad signature: refused."""
    import base64

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key_b64 = getattr(settings, "SENDGRID_WEBHOOK_PUBLIC_KEY", "")
    if not key_b64 or not signature or not timestamp.isdigit():
        return False
    if abs(now - int(timestamp)) > MAX_SKEW_SECONDS:
        return False
    try:
        key = serialization.load_der_public_key(base64.b64decode(key_b64))
        if not isinstance(key, ec.EllipticCurvePublicKey):
            return False
        key.verify(base64.b64decode(signature), timestamp.encode() + body,
                   ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def receipts_from(events: Any) -> list[tuple[str, str, float]]:
    """(our email ref, status, when) for each event we care about; everything else is ignored."""
    out: list[tuple[str, str, float]] = []
    if not isinstance(events, list):
        return out
    for e in events[:1000]:
        if not isinstance(e, dict):
            continue
        status = EVENT_STATUS.get(str(e.get("event", "")))
        ref = e.get("zoiko_ref")
        when = e.get("timestamp")
        if status and isinstance(ref, str) and isinstance(when, (int, float)):
            out.append((ref, status, float(when)))
    return out
