"""Field-level encryption for health and identity data (AES-256-GCM).

What this protects: a value is stored only as ciphertext, so a database copy, a backup or a
mistaken query shows nothing readable. What it does NOT replace: the access rules (policy engine)
still decide who may ask for the plain value.

How it works
- A MASTER key per key id comes from the environment (`ZOIKO_FIELD_KEYS`, "id:base64,id:base64").
  The current id (`ZOIKO_FIELD_KEY_ID`) encrypts; every listed id can still decrypt, so a key can
  be rotated by adding a new one. `needs_rotation` says which values still use an old key, but
  nothing re-encrypts them automatically yet (a re-encrypt job is not built).
- Each customer (tenant) gets its OWN data key, derived from the master key with HKDF, so one
  customer's key never opens another's data.
- Each value is bound to where it lives with AES-GCM "associated data": tenant, table, field and
  the row id. A ciphertext copied to another row, field or customer fails to decrypt.
- A fresh random nonce is used every time, so the same name encrypts differently each time.

Token format: `zf1:<key id>:<base64url(nonce + ciphertext)>`.

Never log a plaintext, a token or a key. Errors carry no detail on purpose.
In production the master key should come from a managed key service (decision DEV-016); until
then it is an environment variable, which is only as safe as that environment.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

PREFIX = "zf1"
KEY_BYTES = 32
NONCE_BYTES = 12
_INFO = b"zoiko-access-field-key-v1"


class CryptoError(Exception):
    """Could not encrypt or decrypt. Deliberately carries no detail (it could leak data)."""


def parse_keys(raw: str) -> dict[str, bytes]:
    """Read "id:base64,id:base64". Raises ValueError (names only) if anything is malformed."""
    keys: dict[str, bytes] = {}
    for part in (p.strip() for p in raw.split(",") if p.strip()):
        key_id, sep, encoded = part.partition(":")
        if not sep or not key_id.isalnum():
            raise ValueError("a field key entry must look like id:base64")
        try:
            key = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("a field key is not valid base64") from None
        if len(key) != KEY_BYTES:
            raise ValueError("a field key must be 32 bytes")
        keys[key_id] = key
    if not keys:
        raise ValueError("no field keys configured")
    return keys


def _keys() -> dict[str, bytes]:
    try:
        return parse_keys(settings.FIELD_KEYS)
    except ValueError:
        raise CryptoError from None


def current_key_id() -> str:
    keys = _keys()
    wanted = getattr(settings, "FIELD_KEY_ID", "") or next(iter(keys))
    if wanted not in keys:
        raise CryptoError
    return str(wanted)


def _tenant_key(master: bytes, tenant_id: uuid.UUID | str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=str(tenant_id).encode(),
                info=_INFO).derive(master)


def _aad(tenant_id: uuid.UUID | str, context: str) -> bytes:
    return f"{tenant_id}|{context}".encode()


def encrypt(value: str, *, tenant_id: uuid.UUID | str, context: str) -> str:
    """Encrypt text for this customer. `context` names the field and row, for example
    `person:<public id>:legal_name`; the same context must be given to decrypt."""
    key_id = current_key_id()
    key = _tenant_key(_keys()[key_id], tenant_id)
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, value.encode("utf-8"), _aad(tenant_id, context))
    return f"{PREFIX}:{key_id}:{base64.urlsafe_b64encode(nonce + sealed).decode('ascii')}"


def decrypt(token: str, *, tenant_id: uuid.UUID | str, context: str) -> str:
    try:
        prefix, key_id, body = token.split(":", 2)
        if prefix != PREFIX:
            raise CryptoError
        master = _keys()[key_id]
        raw = base64.urlsafe_b64decode(body.encode("ascii"))
        nonce, sealed = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
        plain = AESGCM(_tenant_key(master, tenant_id)).decrypt(
            nonce, sealed, _aad(tenant_id, context))
        return plain.decode("utf-8")
    except (ValueError, KeyError, InvalidTag, binascii.Error, UnicodeError):
        raise CryptoError from None


def blind_index(value: str, *, tenant_id: uuid.UUID | str, context: str) -> str:
    """A keyed fingerprint of `value` for this customer and purpose: equal values give equal
    fingerprints, so they can be FOUND without decrypting anything, and the fingerprint reveals
    nothing else (HMAC-SHA256 under a key derived for this customer and this `context`). It uses
    the CURRENT key, so after a key rotation the fingerprints must be rebuilt, like re-encrypting
    (`manage.py rebuild_match_keys`)."""
    customer_key = _tenant_key(_keys()[current_key_id()], tenant_id)
    purpose_key = hmac.new(
        customer_key, b"blind-index|" + context.encode(), hashlib.sha256).digest()
    return hmac.new(purpose_key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def needs_rotation(token: str) -> bool:
    """True if this value was sealed with an older key than the current one."""
    try:
        return token.split(":", 2)[1] != current_key_id()
    except IndexError:
        return True
