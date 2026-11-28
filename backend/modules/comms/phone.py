"""Proving a patient's phone number is theirs before anything is texted to it (DEV-198).

The patient asks for a code; a six-digit code is texted to the number on their own profile (the
"code" template, DEV-194). They type it back. Only then are texts sent to that number. Neither the
code nor the number is stored: only keyed fingerprints of them (`foundation.crypto.blind_index`),
so a database reader learns neither. A code lasts 10 minutes and allows 5 tries, and at most 3 codes
are sent an hour. Verification belongs to one number: if the profile number changes, it no longer
matches and texts stop until the new number is checked. (A key rotation also ends it, because the
fingerprints use the current key; the patient simply checks again.)
"""
from __future__ import annotations

import datetime as dt
import secrets
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.crypto import blind_index
from foundation.errors import ErrorCode, ZoikoError
from foundation.texts import queue_text
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .market import texts_allowed
from .models import EmailPreference, PhoneCheck

CODE_LIFE = dt.timedelta(minutes=10)
MAX_TRIES = 5
MAX_SENDS_PER_HOUR = 3


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _clean(number: str) -> str:
    return (number or "").replace(" ", "")


def _number_digest(number: str) -> str:
    return blind_index(_clean(number), tenant_id=require_context().tenant_id,
                       context="comms:phone-number")


def _code_digest(code: str, check_ref: str) -> str:
    return blind_index(code, tenant_id=require_context().tenant_id,
                       context=f"comms:phone-code:{check_ref}")


def phone_verified(actor_ref: str, number: str) -> bool:
    """Has this login proved that it holds exactly this number?"""
    if not number:
        return False
    pref = EmailPreference.objects.filter(actor_ref=actor_ref, **_scope()).first()
    return bool(pref and pref.phone_verified_at and pref.phone_digest
                and pref.phone_digest == _number_digest(number))


def send_code(actor_ref: str, number: str, clock: Clock | None = None) -> dict[str, Any]:
    """Text a fresh code to this number (the caller passes the patient's OWN profile number)."""
    now = utc_now(clock)
    if not texts_allowed():
        raise ZoikoError(ErrorCode.JURISDICTION_INACTIVE)     # DEV-228: market not switched on
    with transaction.atomic():
        recent = PhoneCheck.objects.select_for_update().filter(
            actor_ref=actor_ref, created_at__gte=now - dt.timedelta(hours=1), **_scope())
        if recent.count() >= MAX_SENDS_PER_HOUR:
            raise ZoikoError(ErrorCode.RATE_LIMITED)
        code = f"{secrets.randbelow(1_000_000):06d}"
        check = PhoneCheck(actor_ref=actor_ref, number_digest=_number_digest(number),
                           code_digest="", expires_at=now + CODE_LIFE, **_scope())
        check.code_digest = _code_digest(code, check.public_id)
        queue_text(kind="phone_check", to_number=_clean(number), template="code",
                   values={"code": code})              # refuses a number of the wrong shape
        check.save()
        record(actor_ref=actor_ref, action="comms.phone_code_sent", object_type="actor",
               object_ref=actor_ref, outcome=AuditOutcome.ALLOWED)
        return {"expires_at": check.expires_at.isoformat()}


def confirm_code(actor_ref: str, number: str, code: object,
                 clock: Clock | None = None) -> bool:
    """Check the typed code against the latest live code for this number. Right: the number is
    verified and True is returned. Wrong: one try is used up and False is returned. The caller
    raises the refusal AFTER its own transaction commits, so the used-up try is kept. None live,
    or too many tries: CONFLICT, and the patient asks for a new code."""
    now = utc_now(clock)
    if not isinstance(code, str) or not code.isdigit() or len(code) != 6:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["code"])
    with transaction.atomic():
        check = PhoneCheck.objects.select_for_update().filter(
            actor_ref=actor_ref, used_at__isnull=True, expires_at__gt=now,
            number_digest=_number_digest(number), **_scope()).order_by("-created_at").first()
        if check is None or check.attempts >= MAX_TRIES:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        check.attempts += 1
        wrong = check.code_digest != _code_digest(code, check.public_id)
        if wrong:
            # The used-up try must be kept, so it is saved and the refusal raised only AFTER this
            # transaction commits (raising inside it would undo the count).
            check.save(update_fields=["attempts", "updated_at"])
            record(actor_ref=actor_ref, action="comms.phone_code_wrong", object_type="actor",
                   object_ref=actor_ref, outcome=AuditOutcome.DENIED)
    if wrong:
        return False
    with transaction.atomic():
        check = PhoneCheck.objects.select_for_update().get(pk=check.pk)
        if check.used_at is not None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        check.used_at = now
        check.save(update_fields=["attempts", "used_at", "updated_at"])
        ctx = require_context()
        EmailPreference.objects.update_or_create(
            actor_ref=actor_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
            defaults={"phone_digest": check.number_digest, "phone_verified_at": now})
        record(actor_ref=actor_ref, action="comms.phone_verified", object_type="actor",
               object_ref=actor_ref, outcome=AuditOutcome.ALLOWED)
        return True
