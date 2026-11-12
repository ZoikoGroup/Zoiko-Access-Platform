"""Sending one signed webhook: the signature, the address guard and the plain HTTP call.

The message is signed over `<unix time>.<delivery id>.<exact body>` with HMAC-SHA256 and the
endpoint's own secret (GHE-13: the timestamp and the delivery id are part of what is signed).
The receiver checks the signature, rejects an old timestamp and ignores a delivery id it has
already seen. A 2xx answer only means "received". Redirects are never followed, and an address that
resolves to a private, loopback or link-local network is refused (GHE-13: SSRF target -> deny).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlsplit

from django.conf import settings

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]
TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 4096


class UnsafeTarget(ValueError):
    """The address is not one we may send to."""


def sign(secret: str, timestamp: int, delivery_id: str, body: bytes) -> str:
    message = f"{timestamp}.{delivery_id}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def signature_header(
    secret: str, key_id: str, timestamp: int, delivery_id: str, body: bytes
) -> str:
    return f"t={timestamp},kid={key_id},v1={sign(secret, timestamp, delivery_id, body)}"


def _allow_private() -> bool:
    return bool(getattr(settings, "WEBHOOK_ALLOW_PRIVATE_TARGETS", False))


def check_url_shape(url: str) -> None:
    """Cheap checks done when an address is registered."""
    parts = urlsplit(url)
    if parts.scheme != "https" and not (_allow_private() and parts.scheme == "http"):
        raise UnsafeTarget("https only")
    if not parts.hostname or parts.username or parts.password or len(url) > 500:
        raise UnsafeTarget("address not accepted")
    if parts.port not in (None, 443) and not _allow_private():
        raise UnsafeTarget("port not accepted")


def check_resolves_public(url: str) -> None:
    """Done again right before every send, because DNS can change after registration."""
    if _allow_private():
        return
    host = urlsplit(url).hostname or ""
    try:
        found = {info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise UnsafeTarget("address does not resolve") from exc
    if not found:
        raise UnsafeTarget("address does not resolve")
    for text in found:
        if not ipaddress.ip_address(text).is_global:
            raise UnsafeTarget("address is on a private network")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    """The real transport. Returns (status, first bytes of the answer)."""
    check_resolves_public(url)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_RESPONSE_BYTES)
