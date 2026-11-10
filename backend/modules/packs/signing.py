"""Signing and checking pack documents (Ed25519). Pure functions, no database.

The signed text is the pack's fields in one fixed layout (sorted keys, no spaces, UTC times), so
the signer and the checker always agree on the bytes. The signature covers the key id too.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from foundation.timeutil import UTC, require_aware

PACK_FIELDS = (
    "country", "version", "issued_at", "valid_from", "expires_at", "revocation_epoch",
    "capabilities", "key_id",
)
NOTICE_FIELDS = ("min_epoch", "issued_at", "key_id")


def iso(value: dt.datetime) -> str:
    """One text form for an instant: UTC with an explicit offset."""
    return require_aware(value).astimezone(UTC).isoformat()


def parse_instant(text: str) -> dt.datetime:
    if not isinstance(text, str):
        raise ValueError("an instant must be text")
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return require_aware(value).astimezone(UTC)


def canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def generate_keypair() -> tuple[str, str]:
    """(private, public) as base64 of the 32 raw bytes. The private half never enters the cell."""
    private = Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw_private).decode(), base64.b64encode(raw_public).decode()


def sign(private_key_b64: str, body: dict[str, Any]) -> str:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    return base64.b64encode(key.sign(canonical(body))).decode()


def verify(public_key_b64: str, body: dict[str, Any], signature_b64: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64, validate=True), canonical(body))
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True


def parse_public_keys(raw: str) -> dict[str, str]:
    """"id:base64,id:base64" -> {id: base64}. An empty setting means no key is trusted."""
    keys: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        key_id, _, value = item.partition(":")
        if not key_id or not value or key_id in keys:
            raise ValueError("pack public keys must look like 'id:base64,id:base64'")
        keys[key_id] = value
    return keys


def build_signed_pack(
    *, private_key_b64: str, key_id: str, country: str, version: int, issued_at: dt.datetime,
    valid_from: dt.datetime, expires_at: dt.datetime, revocation_epoch: int,
    capabilities: dict[str, Any],
) -> dict[str, Any]:
    """What the global plane publishes. Used by tooling and tests, never by the cell itself."""
    body: dict[str, Any] = {
        "country": country, "version": version, "issued_at": iso(issued_at),
        "valid_from": iso(valid_from), "expires_at": iso(expires_at),
        "revocation_epoch": revocation_epoch, "capabilities": capabilities, "key_id": key_id,
    }
    return {**body, "signature": sign(private_key_b64, body)}


def build_signed_notice(
    *, private_key_b64: str, key_id: str, min_epoch: int, issued_at: dt.datetime
) -> dict[str, Any]:
    body: dict[str, Any] = {"min_epoch": min_epoch, "issued_at": iso(issued_at), "key_id": key_id}
    return {**body, "signature": sign(private_key_b64, body)}
