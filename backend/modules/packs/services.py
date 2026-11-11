"""Installing, revoking and consulting country packs. Everything fails closed.

A capability is on only when ALL of these hold right now: a pack exists for the country, its
signature checks against a trusted key, today lies inside its validity window, its revocation epoch
is not below the cell's floor, and the pack says the capability is enabled. Anything else (no pack,
unknown key, expired, revoked, tampered row) means off.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now

from .models import CountryPack, PackStatus, RevocationState
from .signing import NOTICE_FIELDS, PACK_FIELDS, canonical, iso, parse_instant, verify
from .signing import parse_public_keys as _parse_keys

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
MAX_CAPABILITIES = 200


class PackRejected(Exception):
    """A pack or notice was refused. `reason` is a short stable code, never free text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ParsedPack:
    country: str
    version: int
    issued_at: dt.datetime
    valid_from: dt.datetime
    expires_at: dt.datetime
    revocation_epoch: int
    capabilities: dict[str, Any]
    key_id: str
    signature: str

    def body(self) -> dict[str, Any]:
        return {
            "country": self.country, "version": self.version, "issued_at": iso(self.issued_at),
            "valid_from": iso(self.valid_from), "expires_at": iso(self.expires_at),
            "revocation_epoch": self.revocation_epoch, "capabilities": self.capabilities,
            "key_id": self.key_id,
        }


def _public_keys() -> dict[str, str]:
    return _parse_keys(getattr(settings, "PACK_PUBLIC_KEYS", ""))


def _check_capabilities(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value or len(value) > MAX_CAPABILITIES:
        raise PackRejected("bad_capabilities")
    for name, spec in value.items():
        if not isinstance(name, str) or not _CAPABILITY.match(name):
            raise PackRejected("bad_capabilities")
        if not isinstance(spec, dict) or not isinstance(spec.get("enabled"), bool):
            raise PackRejected("bad_capabilities")
    return value


def _whole_number(value: Any, reason: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PackRejected(reason)
    return value


def _instant(value: Any, reason: str) -> dt.datetime:
    try:
        return parse_instant(value)
    except (ValueError, TypeError):
        raise PackRejected(reason) from None


def _epoch_floor() -> int:
    state = RevocationState.objects.filter(cell_id=settings.ZOIKO_CELL_ID).first()
    return state.min_epoch if state else 0


def _check_signature(body: dict[str, Any], signature: str, key_id: str) -> None:
    key = _public_keys().get(key_id)
    if key is None:
        raise PackRejected("unknown_key")
    if not isinstance(signature, str) or not verify(key, body, signature):
        raise PackRejected("bad_signature")


def parse_document(document: Any) -> ParsedPack:
    """Structure only (no trust yet)."""
    if not isinstance(document, dict) or set(document) != {*PACK_FIELDS, "signature"}:
        raise PackRejected("bad_document")
    country = document["country"]
    if not isinstance(country, str) or not _COUNTRY.match(country):
        raise PackRejected("bad_country")
    key_id = document["key_id"]
    if not isinstance(key_id, str) or not _KEY_ID.match(key_id):
        raise PackRejected("unknown_key")
    pack = ParsedPack(
        country=country,
        version=_whole_number(document["version"], "bad_version", 1),
        issued_at=_instant(document["issued_at"], "bad_dates"),
        valid_from=_instant(document["valid_from"], "bad_dates"),
        expires_at=_instant(document["expires_at"], "bad_dates"),
        revocation_epoch=_whole_number(document["revocation_epoch"], "bad_epoch"),
        capabilities=_check_capabilities(document["capabilities"]),
        key_id=key_id, signature=str(document["signature"]))
    if pack.expires_at <= pack.valid_from:
        raise PackRejected("bad_dates")
    return pack


def verify_document(document: Any, *, clock: Clock | None = None) -> ParsedPack:
    """Structure, signature, validity window and revocation epoch. Raises PackRejected."""
    pack = parse_document(document)
    _check_signature(pack.body(), pack.signature, pack.key_id)
    now = utc_now(clock)
    if now < pack.valid_from:
        raise PackRejected("not_yet_valid")
    if now >= pack.expires_at:
        raise PackRejected("expired")
    if pack.revocation_epoch < _epoch_floor():
        raise PackRejected("revoked")
    return pack


def _row_as_pack(row: CountryPack) -> ParsedPack:
    return ParsedPack(
        country=row.country, version=row.version, issued_at=row.issued_at,
        valid_from=row.valid_from, expires_at=row.expires_at,
        revocation_epoch=row.revocation_epoch, capabilities=row.capabilities,
        key_id=row.key_id, signature=row.signature)


def pack_dict(row: CountryPack) -> dict[str, Any]:
    return {
        "public_id": row.public_id, "country": row.country, "version": row.version,
        "status": row.status, "valid_from": iso(row.valid_from), "expires_at": iso(row.expires_at),
        "revocation_epoch": row.revocation_epoch, "key_id": row.key_id,
        "capabilities": sorted(n for n, s in row.capabilities.items() if s.get("enabled")),
    }


def install_pack(document: Any, *, clock: Clock | None = None) -> dict[str, Any]:
    """Verify a pack and make it the active one for its country. A pack must be NEWER than every
    pack already installed for that country (no rolling back to an older, weaker one). Installing
    the very same pack again changes nothing."""
    pack = verify_document(document, clock=clock)
    digest = hashlib.sha256(canonical(pack.body())).hexdigest()
    cell = settings.ZOIKO_CELL_ID
    with transaction.atomic():
        existing = CountryPack.objects.select_for_update().filter(
            cell_id=cell, country=pack.country)
        newest = existing.order_by("-version").first()
        if newest is not None and newest.version == pack.version:
            if newest.body_digest == digest and newest.status == PackStatus.ACTIVE:
                return pack_dict(newest)
            raise PackRejected("version_not_newer")
        if newest is not None and newest.version > pack.version:
            raise PackRejected("version_not_newer")
        existing.filter(status=PackStatus.ACTIVE).update(status=PackStatus.SUPERSEDED)
        try:
            row = CountryPack.objects.create(
                cell_id=cell, country=pack.country, version=pack.version,
                issued_at=pack.issued_at, valid_from=pack.valid_from, expires_at=pack.expires_at,
                revocation_epoch=pack.revocation_epoch, capabilities=pack.capabilities,
                key_id=pack.key_id, signature=pack.signature, body_digest=digest,
                installed_at=utc_now(clock))
        except IntegrityError:
            raise PackRejected("version_not_newer") from None
    return pack_dict(row)


def apply_revocation_notice(notice: Any, *, clock: Clock | None = None) -> dict[str, Any]:
    """A signed notice raises the cell's revocation floor. Packs below it stop working at once
    (and are marked revoked). The floor never goes down."""
    if not isinstance(notice, dict) or set(notice) != {*NOTICE_FIELDS, "signature"}:
        raise PackRejected("bad_document")
    min_epoch = _whole_number(notice["min_epoch"], "bad_epoch")
    key_id = notice["key_id"]
    if not isinstance(key_id, str) or not _KEY_ID.match(key_id):
        raise PackRejected("unknown_key")
    issued = _instant(notice["issued_at"], "bad_dates")
    _check_signature(
        {"min_epoch": min_epoch, "issued_at": iso(issued), "key_id": key_id},
        str(notice["signature"]), key_id)
    cell = settings.ZOIKO_CELL_ID
    with transaction.atomic():
        state, _ = RevocationState.objects.select_for_update().get_or_create(cell_id=cell)
        if min_epoch < state.min_epoch:
            raise PackRejected("epoch_goes_down")
        state.min_epoch = min_epoch
        state.updated_at = utc_now(clock)
        state.save(update_fields=["min_epoch", "updated_at"])
        revoked = CountryPack.objects.filter(
            cell_id=cell, revocation_epoch__lt=min_epoch).exclude(
            status=PackStatus.REVOKED).update(status=PackStatus.REVOKED)
    return {"min_epoch": min_epoch, "revoked_packs": revoked}


def active_pack(country: str, *, clock: Clock | None = None) -> dict[str, Any] | None:
    """The pack in force for a country, or None. Re-checked on every call, so an expired, revoked
    or tampered pack simply stops counting."""
    if not isinstance(country, str) or not _COUNTRY.match(country):
        return None
    row = CountryPack.objects.filter(
        cell_id=settings.ZOIKO_CELL_ID, country=country, status=PackStatus.ACTIVE
    ).order_by("-version").first()
    if row is None:
        return None
    try:
        pack = _row_as_pack(row)
        _check_signature(pack.body(), pack.signature, pack.key_id)
    except PackRejected:
        return None
    now = utc_now(clock)
    if not pack.valid_from <= now < pack.expires_at or pack.revocation_epoch < _epoch_floor():
        return None
    return pack_dict(row)


def enabled_capabilities(country: str, *, clock: Clock | None = None) -> list[str]:
    found = active_pack(country, clock=clock)
    return list(found["capabilities"]) if found else []


def capability_enabled(country: str, capability: str, *, clock: Clock | None = None) -> bool:
    return capability in enabled_capabilities(country, clock=clock)


def require_capability(country: str, capability: str, *, clock: Clock | None = None) -> None:
    """For code that must not run in a market where the capability is off."""
    if not capability_enabled(country, capability, clock=clock):
        raise ZoikoError(ErrorCode.JURISDICTION_INACTIVE)


def list_packs(country: str = "", *, clock: Clock | None = None) -> list[dict[str, Any]]:
    """Every installed pack, newest first, each with whether it is in force RIGHT NOW (its
    signature, dates and revocation epoch all check out), not just what the table says."""
    rows = CountryPack.objects.filter(cell_id=settings.ZOIKO_CELL_ID)
    if country:
        rows = rows.filter(country=country)
    in_force = {
        c: (active_pack(c, clock=clock) or {}).get("public_id")
        for c in {r.country for r in rows}}
    return [pack_dict(r) | {"in_force": in_force.get(r.country) == r.public_id}
            for r in rows.order_by("country", "-version")]


def cell_status() -> dict[str, Any]:
    """What this cell trusts: the revocation floor and the ids (never the keys) of the signing
    keys it accepts."""
    return {"revocation_floor": _epoch_floor(), "trusted_key_ids": sorted(_public_keys())}
