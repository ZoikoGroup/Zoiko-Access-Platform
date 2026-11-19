"""A clinician's own mobile number for urgent alerts (DEV-334, LEAD DECISION: text messages).

A doctor (or other clinic staff) may give their own mobile number, prove they hold it with a code
(the same check patients use, DEV-198), and turn on a text when a result they ordered comes back
critical. The number is stored ENCRYPTED; only its keyed fingerprint is compared. The text uses the
fixed "urgent" wording: it never says what the result is, or about whom.
"""

from __future__ import annotations

import re
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.texts import mask_number, queue_text
from modules.evidence.api import AuditOutcome, record

from .market import texts_allowed
from .models import EmailPreference
from .phone import confirm_code, phone_verified, send_code

_NUMBER = re.compile(r"^\+[1-9][0-9]{7,14}$")


def _context(actor_ref: str) -> str:
    return f"comms:alert-phone:{actor_ref}"


def _pref(actor_ref: str) -> EmailPreference:
    ctx = require_context()
    pref, _ = EmailPreference.objects.get_or_create(
        actor_ref=actor_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
    return pref


def alert_number(actor_ref: str) -> str:
    """The clinician's own alert number, or "" if none (or unreadable)."""
    ctx = require_context()
    pref = EmailPreference.objects.filter(
        actor_ref=actor_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    if pref is None or not pref.alert_phone_enc:
        return ""
    try:
        return decrypt(pref.alert_phone_enc, tenant_id=ctx.tenant_id, context=_context(actor_ref))
    except CryptoError:
        return ""


def status(actor_ref: str) -> dict[str, Any]:
    number = alert_number(actor_ref)
    ctx = require_context()
    pref = EmailPreference.objects.filter(
        actor_ref=actor_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    return {
        "number": mask_number(number) if number else "",
        "verified": bool(number) and phone_verified(actor_ref, number),
        "critical_texts": bool(pref and pref.critical_texts),
        "hold_texts": bool(pref and pref.hold_texts),
        "texts_available": texts_allowed(),
    }


def set_number(actor_ref: str, number: Any) -> dict[str, Any]:
    """Save the clinician's own mobile number (international form) and text a code to it. A new
    number must be proved again; until then no alert is texted."""
    clean = number.replace(" ", "") if isinstance(number, str) else ""
    if not _NUMBER.match(clean):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["number"])
    with transaction.atomic():
        pref = _pref(actor_ref)
        pref.alert_phone_enc = encrypt(clean, tenant_id=require_context().tenant_id,
                                       context=_context(actor_ref))
        pref.save(update_fields=["alert_phone_enc", "updated_at"])
        record(actor_ref=actor_ref, action="comms.alert_phone_set", object_type="actor",
               object_ref=actor_ref, outcome=AuditOutcome.ALLOWED)
    sent = send_code(actor_ref, clean)
    return {**status(actor_ref), **sent}


def confirm(actor_ref: str, code: Any) -> bool:
    number = alert_number(actor_ref)
    if not number:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    return confirm_code(actor_ref, number, code)


def set_critical_texts(actor_ref: str, enabled: Any) -> dict[str, Any]:
    if not isinstance(enabled, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["critical_texts"])
    with transaction.atomic():
        pref = _pref(actor_ref)
        pref.critical_texts = enabled
        pref.save(update_fields=["critical_texts", "updated_at"])
        record(actor_ref=actor_ref,
               action="comms.critical_texts_on" if enabled else "comms.critical_texts_off",
               object_type="actor", object_ref=actor_ref, outcome=AuditOutcome.ALLOWED)
    return status(actor_ref)


def set_hold_texts(actor_ref: str, enabled: Any) -> dict[str, Any]:
    if not isinstance(enabled, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["hold_texts"])
    with transaction.atomic():
        pref = _pref(actor_ref)
        pref.hold_texts = enabled
        pref.save(update_fields=["hold_texts", "updated_at"])
        record(actor_ref=actor_ref,
               action="comms.hold_texts_on" if enabled else "comms.hold_texts_off",
               object_type="actor", object_ref=actor_ref, outcome=AuditOutcome.ALLOWED)
    return status(actor_ref)


def text_hold(actor_ref: str, day: Any) -> int:
    """The daily "needs your attention" text to an administrator about requests held over 3 days
    (DEV-336): only if they asked, proved their number, texts are on for the market, and not yet
    today. Returns 1 if queued. Call inside a transaction with the clinic's tenant bound."""
    ctx = require_context()
    pref = EmailPreference.objects.select_for_update().filter(
        actor_ref=actor_ref, hold_texts=True, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    if pref is None or pref.hold_texted_on == day:
        return 0
    number = alert_number(actor_ref)
    if not number or not phone_verified(actor_ref, number) or not texts_allowed():
        return 0
    try:
        queue_text(kind="hold_escalation", to_number=number, template="urgent_staff")
    except ZoikoError:
        return 0
    pref.hold_texted_on = day
    pref.save(update_fields=["hold_texted_on", "updated_at"])
    return 1


def text_critical(actor_ref: str) -> int:
    """Queue the urgent text to this clinician, if they asked for it and proved their number, and
    texts are switched on for this market. Returns 1 if queued."""
    ctx = require_context()
    pref = EmailPreference.objects.filter(
        actor_ref=actor_ref, critical_texts=True, cell_id=ctx.cell_id,
        tenant_id=ctx.tenant_id).first()
    if pref is None:
        return 0
    number = alert_number(actor_ref)
    if not number or not phone_verified(actor_ref, number) or not texts_allowed():
        return 0
    try:
        queue_text(kind="critical_result", to_number=number, template="urgent")
    except ZoikoError:
        return 0
    return 1
