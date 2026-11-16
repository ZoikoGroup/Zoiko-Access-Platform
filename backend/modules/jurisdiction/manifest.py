"""The typed content of a country pack (GHE-04 section 03) and its fingerprint.

Only what the system can really enforce or show is here. Everything a regulator might want is NOT
here (licences, contracts, private legal memos, patient data): the specification says those stay in
the jurisdiction's own evidence vault, and a pack only carries a status per evidence kind.
Anything unknown is refused rather than ignored, so a typo cannot silently become a setting.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import zoneinfo
from typing import Any

from foundation.errors import ErrorCode, ZoikoError

# What a pack may switch on. The last three are not built anywhere, so they can only be off.
CAPABILITIES = ("provider_directory", "booking", "messaging", "referrals", "waiting_list",
                "diagnostics", "clinical_ai", "record_exchange")
NOT_BUILT = frozenset({"diagnostics", "clinical_ai", "record_exchange"})
EVIDENCE_KINDS = ("legal", "dpia", "cyber", "clinical")
EVIDENCE_STATES = ("PENDING", "PROVIDED", "NOT_APPLICABLE_REVIEW_NEEDED")
REQUIRED_TO_SIGN = ("legal", "dpia", "cyber")      # clinical may be "needs review", never PENDING
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_SLUG = re.compile(r"^[a-z][a-z0-9-]{2,60}$")
_SERVICE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_NUMBER = re.compile(r"^\+?[0-9]{2,15}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def check_identity(pack_ref: str, country: str, service: str) -> None:
    if not _SLUG.match(pack_ref or ""):
        raise _bad("pack_ref")
    if not _COUNTRY.match(country or ""):
        raise _bad("country")
    if not _SERVICE.match(service or ""):
        raise _bad("service")


def normalize(raw: Any) -> dict[str, Any]:
    """Return the manifest in one canonical shape, or refuse it. Unknown keys are refused."""
    if not isinstance(raw, dict) or set(raw) - {"settings", "capabilities", "evidence", "validity"}:
        raise _bad("manifest")
    settings = raw.get("settings") or {}
    if not isinstance(settings, dict) or set(settings) - {
        "data_request_days", "emergency_number", "default_language", "time_zone"
    }:
        raise _bad("settings")
    days = settings.get("data_request_days")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int)
                             or not 1 <= days <= 365):
        raise _bad("settings.data_request_days")
    number = settings.get("emergency_number") or ""
    if number and not _NUMBER.match(str(number)):
        raise _bad("settings.emergency_number")
    language = settings.get("default_language") or ""
    if language and not _LANGUAGE.match(str(language)):
        raise _bad("settings.default_language")
    zone = settings.get("time_zone") or ""
    if zone:
        try:
            zoneinfo.ZoneInfo(str(zone))
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
            raise _bad("settings.time_zone") from None

    given = raw.get("capabilities") or {}
    if not isinstance(given, dict) or set(given) - set(CAPABILITIES):
        raise _bad("capabilities")
    capabilities = {}
    for name in CAPABILITIES:
        value = given.get(name, False)
        if not isinstance(value, bool) or (value and name in NOT_BUILT):
            raise _bad(f"capabilities.{name}")
        capabilities[name] = value

    given_ev = raw.get("evidence") or {}
    if not isinstance(given_ev, dict) or set(given_ev) - set(EVIDENCE_KINDS):
        raise _bad("evidence")
    evidence = {}
    for kind in EVIDENCE_KINDS:
        value = given_ev.get(kind, "PENDING")
        if value not in EVIDENCE_STATES:
            raise _bad(f"evidence.{kind}")
        evidence[kind] = value

    validity = raw.get("validity") or {}
    if not isinstance(validity, dict) or set(validity) - {"expires_on"}:
        raise _bad("validity")
    expires = validity.get("expires_on")
    if expires is not None:
        try:
            dt.date.fromisoformat(str(expires))
        except ValueError:
            raise _bad("validity.expires_on") from None
        expires = str(expires)
    return {
        "settings": {"data_request_days": days, "emergency_number": str(number),
                     "default_language": str(language), "time_zone": str(zone)},
        "capabilities": capabilities, "evidence": evidence, "validity": {"expires_on": expires},
    }


def ready_to_sign(manifest: dict[str, Any]) -> list[str]:
    """What is still missing before a pack may be signed (empty = nothing missing)."""
    missing = [f"evidence.{k}" for k in REQUIRED_TO_SIGN if manifest["evidence"][k] != "PROVIDED"]
    if manifest["evidence"]["clinical"] == "PENDING":
        missing.append("evidence.clinical")
    if not manifest["validity"]["expires_on"]:
        missing.append("validity.expires_on")       # expiry must be machine-enforced
    return missing


def fingerprint(pack_ref: str, country: str, service: str, revision: int,
                manifest: dict[str, Any]) -> str:
    body = {"pack_ref": pack_ref, "country": country, "service": service,
            "revision": revision, "manifest": manifest}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
