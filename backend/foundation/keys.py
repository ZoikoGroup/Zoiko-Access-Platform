"""Where the field-encryption master keys live, and how a lost key is recovered (GHE-05, ADR-10).

`crypto.py` seals values with a master key per key id. This module is the one place that decides
WHERE those keys come from, so a deployment can move from a development setting to a managed key
service without touching any caller:

  env      the keys are in an environment variable (`ZOIKO_FIELD_KEYS`). Development only: it is
           only as safe as the environment around it.
  file     a key ring file (`ZOIKO_FIELD_KEY_FILE`, JSON) that only the service account can read.
           The file is refused if other users can read it.
  wrapped  a key ring file whose keys are each SEALED by a key-encryption key (KEK) that a managed
           key service (KMS or HSM) holds. The service unwraps them at start-up and on refresh, so
           the file alone, a backup or a copied disk opens nothing. The KEK comes from a pluggable
           "unwrapper": the built-in one reads the KEK from `ZOIKO_KEY_WRAP_KEK` (a stand-in for a
           real service); a real service registers its own with `register_unwrapper`.

If every copy of a key is lost, the values it sealed are unreadable for ever. So a key ring can be
split into shares (Shamir secret sharing): any `threshold` of the shares rebuild it, fewer reveal
nothing. Give the shares to different people, and the loss of one or two no longer matters.

No key, share or key-encryption key is ever logged or returned: status shows only a short
fingerprint of each key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

KEY_BYTES = 32
NONCE_BYTES = 12
PROVIDERS = ("env", "file", "wrapped")
REFRESH_SECONDS = 60.0              # a rotated or re-wrapped key is picked up within a minute
SHARE_PREFIX = "zk1"
MAX_SHARES = 16


class KeyRingError(Exception):
    """A key ring could not be loaded. Carries no detail on purpose (it may be near a secret)."""


@dataclass(frozen=True)
class KeyRing:
    keys: dict[str, bytes]
    current: str
    source: str                      # env | file | wrapped

    def fingerprints(self) -> dict[str, str]:
        return {kid: fingerprint(key) for kid, key in self.keys.items()}


def fingerprint(key: bytes) -> str:
    """A short label that identifies a key without revealing it (eight hex characters)."""
    return hashlib.sha256(b"zoiko-key-fingerprint|" + key).hexdigest()[:8]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text.encode("ascii") + b"=" * (-len(text) % 4))
    except (ValueError, binascii.Error, UnicodeError):
        raise KeyRingError from None


# ------------------------------------------------------------------ unwrappers
Unwrapper = Callable[[str, bytes], bytes]          # (key id, wrapped bytes) -> key bytes
_UNWRAPPERS: dict[str, Unwrapper] = {}


def register_unwrapper(name: str, fn: Unwrapper) -> None:
    """A managed key service (KMS, HSM) plugs in here: it receives the wrapped key and returns the
    plain key, authenticating the key id."""
    _UNWRAPPERS[name] = fn


def _kek() -> bytes:
    raw = getattr(settings, "KEY_WRAP_KEK", "")
    key = _unb64(raw) if raw else b""
    if len(key) != KEY_BYTES:
        raise KeyRingError
    return key


def wrap_with_kek(key_id: str, key: bytes, kek: bytes) -> str:
    """Seal one master key under a key-encryption key. Bound to the key id."""
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(kek).encrypt(nonce, key, b"zoiko-key|" + key_id.encode())
    return _b64(nonce + sealed)


def _unwrap_with_env_kek(key_id: str, wrapped: bytes) -> bytes:
    try:
        return AESGCM(_kek()).decrypt(
            wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], b"zoiko-key|" + key_id.encode())
    except (InvalidTag, ValueError):
        raise KeyRingError from None


register_unwrapper("env-kek", _unwrap_with_env_kek)


# ------------------------------------------------------------------ loading
def _read_ring_file(path: str) -> dict[str, Any]:
    p = Path(path)
    try:
        info = p.stat()
        if os.name == "posix" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise KeyRingError                       # readable by others: refuse to use it
        data = json.loads(p.read_text(encoding="utf8"))
    except (OSError, ValueError):
        raise KeyRingError from None
    if not isinstance(data, dict) or not isinstance(data.get("keys"), dict) or not data["keys"]:
        raise KeyRingError
    return data


def _check_key(raw: bytes) -> bytes:
    if len(raw) != KEY_BYTES:
        raise KeyRingError
    return raw


def _load_uncached() -> KeyRing:
    provider = getattr(settings, "FIELD_KEY_PROVIDER", "env") or "env"
    if provider not in PROVIDERS:
        raise KeyRingError
    wanted = getattr(settings, "FIELD_KEY_ID", "") or ""
    if provider == "env":
        from foundation.crypto import parse_keys  # the env format is defined beside crypto
        try:
            keys = parse_keys(settings.FIELD_KEYS)
        except ValueError:
            raise KeyRingError from None
        current = wanted or next(iter(keys))
    else:
        path = getattr(settings, "FIELD_KEY_FILE", "")
        if not path:
            raise KeyRingError
        data = _read_ring_file(path)
        keys = {}
        for kid, text in data["keys"].items():
            if not isinstance(kid, str) or not isinstance(text, str):
                raise KeyRingError
            raw = _unb64(text)
            if provider == "wrapped":
                unwrap = _UNWRAPPERS.get(getattr(settings, "KEY_UNWRAPPER", "env-kek") or "env-kek")
                if unwrap is None:
                    raise KeyRingError
                raw = unwrap(kid, raw)
            keys[kid] = _check_key(raw)
        current = wanted or str(data.get("current") or next(iter(keys)))
    if current not in keys:
        raise KeyRingError
    return KeyRing(keys, current, provider)


_cache: dict[str, Any] = {"at": 0.0, "ring": None, "sig": None}


def _signature() -> tuple[Any, ...]:
    return (getattr(settings, "FIELD_KEY_PROVIDER", "env"), getattr(settings, "FIELD_KEYS", ""),
            getattr(settings, "FIELD_KEY_ID", ""), getattr(settings, "FIELD_KEY_FILE", ""),
            getattr(settings, "KEY_WRAP_KEK", ""), getattr(settings, "KEY_UNWRAPPER", ""))


def load_ring() -> KeyRing:
    """The current key ring. Cached for a minute (so a service is not asked for every value) and
    reloaded at once if any key setting changes."""
    now = time.monotonic()
    sig = _signature()
    ring = _cache["ring"]
    if ring is None or _cache["sig"] != sig or now - _cache["at"] > REFRESH_SECONDS:
        ring = _load_uncached()
        _cache.update(at=now, ring=ring, sig=sig)
    return ring


def reset_cache() -> None:
    _cache.update(at=0.0, ring=None, sig=None)


def describe() -> dict[str, Any]:
    """What an operator may see: where the keys come from, which key encrypts, and a fingerprint of
    each. Never a key. A problem is reported as `ok: False`, with no detail."""
    try:
        ring = _load_uncached()
    except KeyRingError:
        return {"ok": False, "provider": getattr(settings, "FIELD_KEY_PROVIDER", "env"),
                "current": "", "keys": [], "warnings": ["The key ring could not be loaded."]}
    warnings = []
    if ring.source == "env":
        warnings.append("Keys are held in an environment variable. Use a key file or a managed "
                        "key service before real patients use this system.")
    if len(ring.keys) == 1:
        warnings.append("Only one key is configured, so there is nothing to rotate to yet.")
    return {"ok": True, "provider": ring.source, "current": ring.current,
            "keys": [{"id": kid, "fingerprint": fp, "current": kid == ring.current}
                     for kid, fp in ring.fingerprints().items()],
            "warnings": warnings}


# ------------------------------------------------------------------ making and storing keys
def new_key_ring(key_id: str, existing: dict[str, bytes] | None = None) -> KeyRing:
    """A ring with one more fresh random key, made current. The old keys stay, so values sealed
    with them still open until `rotate_field_keys` has moved them across."""
    keys = dict(existing or {})
    if key_id in keys or not key_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("choose a new key id of letters, digits, - or _")
    keys[key_id] = secrets.token_bytes(KEY_BYTES)
    return KeyRing(keys, key_id, "file")


def ring_to_json(ring: KeyRing, *, kek: bytes | None = None) -> str:
    body = {kid: (wrap_with_kek(kid, key, kek) if kek else _b64(key))
            for kid, key in ring.keys.items()}
    return json.dumps({"current": ring.current, "keys": body}, indent=2, sort_keys=True)


def write_ring_file(path: str, ring: KeyRing, *, kek: bytes | None = None) -> None:
    """Write a key ring so that only its owner can read it."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(p, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as handle:
        handle.write(ring_to_json(ring, kek=kek))
    if os.name == "posix":
        os.chmod(p, 0o600)


# ------------------------------------------------------------------ recovery: Shamir sharing
# Arithmetic in GF(2^8) with the AES polynomial. A secret is split byte by byte.
def _gmul(a: int, b: int) -> int:
    out = 0
    while b:
        if b & 1:
            out ^= a
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
        b >>= 1
    return out


def _ginv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError
    result = 1
    for _ in range(254):          # a^254 = a^-1 in GF(256)
        result = _gmul(result, a)
    return result


def _eval(coeffs: list[int], x: int) -> int:
    y = 0
    for c in reversed(coeffs):
        y = _gmul(y, x) ^ c
    return y


def _secret_check(secret: bytes) -> str:
    return hashlib.sha256(b"zoiko-share-check|" + secret).hexdigest()[:8]


def split_secret(secret: bytes, *, threshold: int, shares: int) -> list[str]:
    """Split `secret` into `shares` text shares; any `threshold` of them rebuild it."""
    if not 2 <= threshold <= shares <= MAX_SHARES or not secret:
        raise ValueError("need 2 <= threshold <= shares <= 16 and a secret")
    polys = [[b] + [secrets.randbelow(256) for _ in range(threshold - 1)] for b in secret]
    check = _secret_check(secret)
    out = []
    for x in range(1, shares + 1):
        y = bytes(_eval(p, x) for p in polys)
        out.append(f"{SHARE_PREFIX}.{threshold}.{x}.{check}.{_b64(y)}")
    return out


def combine_shares(shares: list[str]) -> bytes:
    """Rebuild the secret from enough shares. Raises if there are too few, they do not belong
    together, or the result does not match the check recorded in the shares."""
    parsed = []
    for s in shares:
        parts = s.strip().split(".")
        if len(parts) != 5 or parts[0] != SHARE_PREFIX:
            raise KeyRingError
        try:
            parsed.append((int(parts[1]), int(parts[2]), parts[3], _unb64(parts[4])))
        except ValueError:
            raise KeyRingError from None
    if not parsed:
        raise KeyRingError
    threshold, _, check, first = parsed[0]
    if any(p[0] != threshold or p[2] != check or len(p[3]) != len(first) for p in parsed):
        raise KeyRingError
    unique = {p[1]: p[3] for p in parsed}
    if len(unique) < threshold:
        raise KeyRingError
    points = list(unique.items())[:threshold]
    secret = bytearray()
    for i in range(len(first)):
        total = 0
        for xi, yi in points:
            num, den = 1, 1
            for xj, _ in points:
                if xj != xi:
                    num = _gmul(num, xj)
                    den = _gmul(den, xi ^ xj)
            total ^= _gmul(yi[i], _gmul(num, _ginv(den)))
        secret.append(total)
    if _secret_check(bytes(secret)) != check:
        raise KeyRingError                  # a wrong or damaged share
    return bytes(secret)


def ring_from_json(text: str, *, kek: bytes | None = None) -> KeyRing:
    """Read a ring from its JSON form (used to restore from shares)."""
    try:
        data = json.loads(text)
        keys: dict[str, bytes] = {}
        for kid, body in data["keys"].items():
            raw = _unb64(body)
            if kek:
                raw = AESGCM(kek).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:],
                                          b"zoiko-key|" + kid.encode())
            keys[kid] = _check_key(raw)
        current = str(data.get("current") or next(iter(keys)))
    except (ValueError, KeyError, TypeError, StopIteration, InvalidTag):
        raise KeyRingError from None
    if current not in keys:
        raise KeyRingError
    return KeyRing(keys, current, "file")
