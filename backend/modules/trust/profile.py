"""A patient's identifying details: legal name, birth date and phone.

They are stored only as ciphertext (foundation/crypto.py). Writing needs a policy RECEIPT for
`write:person`; reading is for callers that already passed `read:person` for each patient (the
same convention as the other domain modules). Values are checked before they are stored, and the
audit entry says WHO changed a profile, never what is in it.
"""
from __future__ import annotations

import datetime as dt
import re

from django.db import transaction

from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.policy import api as policy

from .models import Person, PersonProfile
from .services import _audit, _own

WRITE_PERSON = "write:person"
MAX_NAME = 120
_PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{5,19}$")


def _context(person: Person, field: str) -> str:
    return f"person:{person.public_id}:{field}"


def _clean_name(value: str) -> str:
    value = " ".join((value or "").split())             # trim and collapse spaces
    if not 1 <= len(value) <= MAX_NAME or any(ord(ch) < 32 for ch in value):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["name"])
    return value


def _clean_birth_date(value: str, today: dt.date) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        born = dt.date.fromisoformat(value)
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["birth_date"]) from None
    if not dt.date(1900, 1, 1) <= born <= today:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["birth_date"])
    return born.isoformat()


def _clean_phone(value: str) -> str:
    value = (value or "").strip()
    if value and not _PHONE.match(value):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["phone"])
    return value


def store_profile(
    person: Person, *, legal_name: str, birth_date: str, phone: str, by: str, today: dt.date
) -> dict[str, str]:
    """Check and save a patient's details, encrypted. The caller has done the access check (the
    receipt in `set_person_profile`, or the family flow that is creating this very record)."""
    ctx = require_context()
    clean = {
        "legal_name": _clean_name(legal_name),
        "birth_date": _clean_birth_date(birth_date, today),
        "phone": _clean_phone(phone),
    }
    sealed = {
        field: (encrypt(text, tenant_id=ctx.tenant_id, context=_context(person, field))
                if text else "")
        for field, text in clean.items()
    }
    row, _ = PersonProfile.objects.get_or_create(
        person=person, defaults={"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id})
    row.legal_name_enc = sealed["legal_name"]
    row.birth_date_enc = sealed["birth_date"]
    row.phone_enc = sealed["phone"]
    row.updated_by_ref = by
    row.entity_version += 1
    row.save(update_fields=["legal_name_enc", "birth_date_enc", "phone_enc",
                            "updated_by_ref", "entity_version", "updated_at"])
    _audit(by, "person.profile_update", "person", person.public_id)
    return clean


def set_person_profile(
    person_public_id: str,
    *,
    legal_name: str,
    birth_date: str = "",
    phone: str = "",
    actor_public_id: str,
    decision: policy.Decision,
    organization_public_id: str | None = None,
    clock: Clock | None = None,
) -> dict[str, str]:
    """Save a patient's details, replacing what was there. The name is required."""
    today = utc_now(clock).date()
    with transaction.atomic():
        person = _own(Person, person_public_id)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_PERSON,
            organization_public_id=organization_public_id, subject_ref=person.public_id)
        return store_profile(
            person, legal_name=legal_name, birth_date=birth_date, phone=phone,
            by=actor_public_id, today=today)


def get_person_profiles(person_public_ids: list[str]) -> dict[str, dict[str, str]]:
    """Plain details for these patients that HAVE a profile: {person id: {name, birth_date,
    phone}}. The caller has already been allowed to read each of them."""
    if not person_public_ids:
        return {}
    ctx = require_context()
    rows = (PersonProfile.objects.select_related("person")
            .filter(person__public_id__in=person_public_ids, cell_id=ctx.cell_id,
                    tenant_id=ctx.tenant_id))
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        person = row.person

        def plain(token: str, field: str, person: Person = person) -> str:
            if not token:
                return ""
            try:
                return decrypt(token, tenant_id=ctx.tenant_id, context=_context(person, field))
            except CryptoError:
                return ""            # unreadable (wrong key, or tampered): show nothing, not junk

        out[person.public_id] = {
            "name": plain(row.legal_name_enc, "legal_name"),
            "birth_date": plain(row.birth_date_enc, "birth_date"),
            "phone": plain(row.phone_enc, "phone"),
        }
    return out


def profile_summary_for(person_public_ids: list[str]) -> dict[str, str]:
    """Only the display name, for lists. Same rule: the caller has already checked access."""
    return {ref: p["name"] for ref, p in get_person_profiles(person_public_ids).items()
            if p["name"]}

