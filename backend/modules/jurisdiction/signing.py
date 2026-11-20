"""Signing a country pack when both approvals are in, and checking that signature on every read
(GHE-16 AT-10, DEV-210). Adapted from the Ed25519 signing on the `maruthi` branch (`packs/signing`).

Why: an approval is a database row, so anyone who can write to the database could otherwise mark a
pack "signed" or change a signed pack's rules. The private key lives only in the server's settings
(`ZOIKO_PACK_SIGNING_KEY`), never in the database, so a forged or edited row fails the check and
the pack counts for nothing (fail closed).

The signed text is a fixed layout (sorted keys, no spaces) of the pack's identity, the hash of its
exact manifest, and the key id, so the signer and the checker always agree on the bytes.

TEMPORARY (decision 4, keys): with no key set, a development key is derived from the Django secret
key, and the release check refuses to start production that way. Who holds the real key, and how it
is rotated, is open; today one key signs and checks, so a new key makes older signatures invalid.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings

from .manifest import fingerprint
from .models import CountryPack

_DERIVE_LABEL = b"zoiko-pack-signing-dev-v1:"


def _private_key() -> Ed25519PrivateKey:
    configured = (getattr(settings, "PACK_SIGNING_KEY", "") or "").strip()
    if configured:
        try:
            seed = base64.b64decode(configured, validate=True)
        except binascii.Error as exc:
            raise ValueError("ZOIKO_PACK_SIGNING_KEY is not base64") from exc
        if len(seed) != 32:
            raise ValueError("ZOIKO_PACK_SIGNING_KEY must be 32 bytes of base64")
    else:
        seed = hashlib.sha256(_DERIVE_LABEL + settings.SECRET_KEY.encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def key_id() -> str:
    """A short public name for the key in use: the start of a hash of its public half."""
    public = _private_key().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(public).hexdigest()[:16]


def generate_key() -> str:
    """A new private key as base64, for `ZOIKO_PACK_SIGNING_KEY`."""
    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return base64.b64encode(raw).decode()


def _body(pack: CountryPack, kid: str) -> bytes:
    content = fingerprint(pack.pack_ref, pack.country, pack.service, pack.revision, pack.manifest)
    body: dict[str, Any] = {
        "pack_ref": pack.pack_ref, "country": pack.country, "service": pack.service,
        "revision": pack.revision, "content_hash": content, "key_id": kid,
        "tenant_id": str(pack.tenant_id), "cell_id": pack.cell_id,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign(pack: CountryPack) -> None:
    """Set the signature fields on this pack (the caller saves it)."""
    kid = key_id()
    pack.signing_key_id = kid
    pack.signature = base64.b64encode(_private_key().sign(_body(pack, kid))).decode()


def is_valid(pack: CountryPack) -> bool:
    """True only when this exact row carries a good signature from the key in use."""
    if not pack.signature or pack.signing_key_id != key_id():
        return False
    try:
        raw = base64.b64decode(pack.signature, validate=True)
        _private_key().public_key().verify(raw, _body(pack, pack.signing_key_id))
    except (binascii.Error, InvalidSignature, ValueError):
        return False
    return True
