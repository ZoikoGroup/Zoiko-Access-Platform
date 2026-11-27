"""AWS SES delivery events, delivered through an SNS topic (decided 27 Nov 2026, DEV-403).

SES publishes each email's outcome (delivered, bounced, rejected, delayed) to an SNS topic, and the
topic posts it to /api/v1/webhooks/ses/events. No sign-in: SNS's signature IS the credential.

* The signature is checked against AWS's own certificate, fetched only from
  https://sns.<region>.amazonaws.com/... and ending in .pem (SignatureVersion 1 is SHA1, 2 SHA256).
* Only the one topic this deployment set up (`SES_EVENTS_TOPIC_ARN`) is accepted; anything else,
  or a bad signature, is refused and nothing in it is used.
* A subscription request for that topic is confirmed by visiting its SubscribeURL (same host rule).
* Only our own email reference (the `zoiko_ref` tag) and the outcome are used; addresses and
  everything else in the event are ignored and never stored.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings

_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com$")
_certs: dict[str, x509.Certificate] = {}
NOTIFICATION_KEYS = ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type")
SUBSCRIBE_KEYS = ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type")
# SES event type (and bounce kind) -> our delivery status (foundation.mail.DELIVERY_STATES).
OUTCOMES = {"Delivery": "delivered", "Reject": "dropped", "DeliveryDelay": "deferred"}


def _aws_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and bool(_SNS_HOST.match(parts.hostname or ""))


def fetch_certificate(url: str) -> x509.Certificate:
    if url not in _certs:
        with urllib.request.urlopen(url, timeout=10) as answer:  # noqa: S310 - host checked
            _certs[url] = x509.load_pem_x509_certificate(answer.read())
    return _certs[url]


def canonical(message: dict[str, Any]) -> bytes:
    keys = NOTIFICATION_KEYS if message.get("Type") == "Notification" else SUBSCRIBE_KEYS
    return "".join(f"{k}\n{message[k]}\n" for k in keys if k in message).encode()


def verify(message: dict[str, Any]) -> bool:
    """True only for a correctly signed message from our own topic."""
    topic = getattr(settings, "SES_EVENTS_TOPIC_ARN", "")
    if not topic or message.get("TopicArn") != topic:
        return False
    cert_url = message.get("SigningCertURL")
    if not _aws_url(cert_url) or not str(cert_url).endswith(".pem"):
        return False
    version = str(message.get("SignatureVersion", ""))
    algorithm: hashes.HashAlgorithm
    if version == "2":
        algorithm = hashes.SHA256()
    elif version == "1":
        algorithm = hashes.SHA1()  # noqa: S303 - what SNS signature version 1 uses
    else:
        return False
    try:
        key = fetch_certificate(str(cert_url)).public_key()
        if not isinstance(key, rsa.RSAPublicKey):
            return False
        key.verify(base64.b64decode(str(message.get("Signature", ""))), canonical(message),
                   padding.PKCS1v15(), algorithm)
    except (InvalidSignature, ValueError, urllib.error.URLError, OSError):
        return False
    return True


def confirm_subscription(message: dict[str, Any]) -> bool:
    url = message.get("SubscribeURL")
    if not _aws_url(url):
        return False
    try:
        with urllib.request.urlopen(str(url), timeout=10) as answer:  # noqa: S310 - host checked
            return bool(answer.status < 300)
    except (urllib.error.URLError, OSError):
        return False


def receipt(message: dict[str, Any]) -> tuple[str, str] | None:
    """(our email reference, outcome) from one SES event, or None when there is nothing to use."""
    try:
        event = json.loads(str(message.get("Message", "")))
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    kind = event.get("eventType") or event.get("notificationType")
    if kind == "Bounce":
        bounce = event.get("bounce") or {}
        status = "bounced" if bounce.get("bounceType") == "Permanent" else "deferred"
    else:
        found = OUTCOMES.get(str(kind))
        if found is None:
            return None                            # complaints, opens and the rest: not used
        status = found
    tags = (event.get("mail") or {}).get("tags") or {}
    refs = tags.get("zoiko_ref") if isinstance(tags, dict) else None
    ref = refs[0] if isinstance(refs, list) and refs else None
    return (str(ref), status) if ref else None
