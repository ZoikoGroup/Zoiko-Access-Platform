"""Signing webhook deliveries, and checking them on the receiving side (what a partner's SDK does).

The signature covers the timestamp AND the body: `v1 = HMAC-SHA256(secret, "<timestamp>." + body)`.
A receiver rejects a delivery whose timestamp is too old or too new, so a captured delivery cannot
be replayed later (GHE-13: signed payloads, replay protection).
"""
from __future__ import annotations

import hashlib
import hmac

TOLERANCE_SECONDS = 300


def sign_body(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"v1={mac.hexdigest()}"


def verify_signature(
    secret: str, *, timestamp: str, body: bytes, signature: str, now: int,
    tolerance: int = TOLERANCE_SECONDS,
) -> bool:
    """True only for a correct signature on a delivery sent within `tolerance` seconds of `now`."""
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(now - sent_at) > tolerance:
        return False
    return hmac.compare_digest(sign_body(secret, sent_at, body), signature or "")
