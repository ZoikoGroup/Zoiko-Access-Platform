"""A doctor's qualifications, and what a clinic lets them do (DEV-357).

A QUALIFICATION is what a doctor holds (a degree, a registration, a specialty). The doctor or a
clinic administrator writes it down; an administrator, never the doctor themselves, records that
they checked it and against what. A checked one is never edited: it is withdrawn and written again.
After its expiry date it counts for nothing. Patients see only checked, unexpired ones, never a
registration number or a note.

A PRIVILEGE is what one clinic allows a doctor to do there: a service of that clinic, from a day,
optionally until a day. It is a record for the clinic's own governance. TEMPORARY RULE: it is not
enforced anywhere (booking and times work as before), because the clinic's policy for that is not
written yet.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import Actor, ActorType, Privilege, Qualification, QualificationStatus
from .name_checks import _doctor_at

KINDS = {
    "degree": "Degree or diploma",
    "registration": "Professional registration or licence",
    "specialty": "Specialty",
    "other": "Other",
}
MAX_PER_DOCTOR = 50


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _text(value: Any, field: str, low: int, high: int) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if (value is not None and not isinstance(value, str)) or not low <= len(text) <= high or any(
            ord(ch) < 32 for ch in text):
        raise _bad(field)
    return text


def _date(value: Any, field: str) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        day = dt.date.fromisoformat(str(value))
    except ValueError:
        raise _bad(field) from None
    if not dt.date(1900, 1, 1) <= day <= dt.date(2200, 1, 1):
        raise _bad(field)
    return day


def _audit(actor: str, action: str, ref: str, reason: str = "") -> None:
    record(actor_ref=actor, action=f"credential.{action}", object_type="credential",
           object_ref=ref, outcome=AuditOutcome.ALLOWED, reason_code=reason)


def _clinician(actor_ref: str) -> Actor:
    actor = Actor.objects.filter(public_id=actor_ref, **_scope()).first()
    if actor is None or actor.actor_type != ActorType.CLINICIAN:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return actor


def qualification_dict(q: Qualification, today: dt.date) -> dict[str, Any]:
    state = q.status
    if state != QualificationStatus.WITHDRAWN and q.expires_on and q.expires_on < today:
        state = "expired"
    return {
        "id": q.public_id, "kind": q.kind, "kind_text": KINDS.get(q.kind, q.kind),
        "title": q.title, "issuer": q.issuer, "reference": q.reference,
        "obtained_on": q.obtained_on.isoformat() if q.obtained_on else None,
        "expires_on": q.expires_on.isoformat() if q.expires_on else None,
        "state": state, "checked_on": q.checked_on.isoformat() if q.checked_on else None,
        "check_note": q.check_note, "withdrawn_reason": q.withdrawn_reason,
    }


def list_for(actor_ref: str, clock: Clock | None = None) -> list[dict[str, Any]]:
    today = utc_now(clock).date()
    rows = Qualification.objects.filter(
        actor__public_id=actor_ref, **_scope()).order_by("created_at")
    return [qualification_dict(q, today) for q in rows]


def add_qualification(
    actor_ref: str, *, kind: Any, title: Any, issuer: Any, reference: Any = "",
    obtained_on: Any = None, expires_on: Any = None, by_ref: str,
    organization_ref: str = "", clock: Clock | None = None,
) -> dict[str, Any]:
    """The doctor for themselves, or (with `organization_ref`) that clinic's administrator for a
    doctor who works there. It starts unchecked."""
    if kind not in KINDS:
        raise _bad("kind")
    name = _text(title, "title", 2, 120)
    who = _text(issuer, "issuer", 2, 120)
    number = _text(reference or "", "reference", 0, 64)
    got, until = _date(obtained_on, "obtained_on"), _date(expires_on, "expires_on")
    today = utc_now(clock).date()
    if got and got > today:
        raise _bad("obtained_on")
    if got and until and until < got:
        raise _bad("expires_on")
    with transaction.atomic():
        actor = (_doctor_at(actor_ref, organization_ref) if organization_ref
                 else _clinician(actor_ref))
        if Qualification.objects.filter(actor=actor, **_scope()).count() >= MAX_PER_DOCTOR:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row = Qualification.objects.create(
            actor=actor, kind=kind, title=name, issuer=who, reference=number, obtained_on=got,
            expires_on=until, added_by_ref=by_ref, **_scope())
        _audit(by_ref, "qualification_added", row.public_id, kind)
        return qualification_dict(row, today)


def decide_qualification(
    actor_ref: str, qualification_ref: str, step: str, *, text: Any, by_ref: str,
    organization_ref: str, clock: Clock | None = None,
) -> dict[str, Any]:
    """verify (text: what was checked) or withdraw (text: why), by an administrator of a clinic
    the doctor works at. Nobody checks their own."""
    if step not in ("verify", "withdraw"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    note = _text(text, "note" if step == "verify" else "reason", 3, 200)
    if step == "verify" and by_ref == actor_ref:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    now = utc_now(clock)
    with transaction.atomic():
        actor = _doctor_at(actor_ref, organization_ref)
        row = Qualification.objects.select_for_update().filter(
            public_id=qualification_ref, actor=actor, **_scope()).first()
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        state = qualification_dict(row, now.date())["state"]
        if step == "verify":
            if state != QualificationStatus.RECORDED:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            row.status, row.checked_by_ref = QualificationStatus.VERIFIED, by_ref
            row.checked_on, row.check_note = now.date(), note
        else:
            if row.status == QualificationStatus.WITHDRAWN:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            row.status, row.withdrawn_by_ref = QualificationStatus.WITHDRAWN, by_ref
            row.withdrawn_reason = note
        row.entity_version += 1
        row.save()
        _audit(by_ref, f"qualification_{step}", row.public_id)
        return qualification_dict(row, now.date())


def shown_to_patients(clock: Clock | None = None) -> dict[str, list[dict[str, Any]]]:
    """doctor ref -> their checked, unexpired qualifications, with nothing a patient need not
    see (no number, no note, no reviewer)."""
    today = utc_now(clock).date()
    out: dict[str, list[dict[str, Any]]] = {}
    rows = Qualification.objects.select_related("actor").filter(
        status=QualificationStatus.VERIFIED, **_scope()).order_by("created_at")
    for q in rows:
        if q.expires_on and q.expires_on < today:
            continue
        out.setdefault(q.actor.public_id, []).append({
            "title": q.title, "issuer": q.issuer, "kind": q.kind,
            "checked_on": q.checked_on.isoformat() if q.checked_on else None})
    return out


# ------------------------------------------------------------------ privileges
def privilege_dict(p: Privilege, today: dt.date) -> dict[str, Any]:
    if p.withdrawn_at:
        state = "withdrawn"
    elif p.valid_until and p.valid_until < today:
        state = "ended"
    elif p.valid_from > today:
        state = "not_started"
    else:
        state = "in_force"
    return {
        "id": p.public_id, "organization": p.organization_ref, "service": p.service_ref,
        "valid_from": p.valid_from.isoformat(),
        "valid_until": p.valid_until.isoformat() if p.valid_until else None,
        "note": p.note, "state": state, "withdrawn_reason": p.withdrawn_reason,
    }


def privileges_for(actor_ref: str, organization_ref: str = "", clock: Clock | None = None
                   ) -> list[dict[str, Any]]:
    today = utc_now(clock).date()
    rows = Privilege.objects.filter(actor__public_id=actor_ref, **_scope())
    if organization_ref:
        rows = rows.filter(organization_ref=organization_ref)
    return [privilege_dict(p, today) for p in rows.order_by("created_at")]


def grant_privilege(
    actor_ref: str, *, organization_ref: str, service_ref: str, valid_from: Any = None,
    valid_until: Any = None, note: Any = "", by_ref: str, clock: Clock | None = None,
) -> dict[str, Any]:
    """A clinic administrator, never for themselves. The caller has checked the service belongs
    to the clinic."""
    if by_ref == actor_ref:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    today = utc_now(clock).date()
    start = _date(valid_from, "valid_from") or today
    until = _date(valid_until, "valid_until")
    if until and (until < start or until < today):
        raise _bad("valid_until")
    words = _text(note or "", "note", 0, 200)
    with transaction.atomic():
        actor = _doctor_at(actor_ref, organization_ref)
        try:
            with transaction.atomic():
                row = Privilege.objects.create(
                    actor=actor, organization_ref=organization_ref, service_ref=service_ref,
                    valid_from=start, valid_until=until, note=words, granted_by_ref=by_ref,
                    **_scope())
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None     # already held
        _audit(by_ref, "privilege_granted", row.public_id)
        return privilege_dict(row, today)


def withdraw_privilege(
    actor_ref: str, privilege_ref: str, *, organization_ref: str, reason: Any, by_ref: str,
    clock: Clock | None = None,
) -> dict[str, Any]:
    why = _text(reason, "reason", 3, 200)
    now = utc_now(clock)
    with transaction.atomic():
        row = Privilege.objects.select_for_update().filter(
            public_id=privilege_ref, actor__public_id=actor_ref,
            organization_ref=organization_ref, **_scope()).first()
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.withdrawn_at:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.withdrawn_at, row.withdrawn_by_ref, row.withdrawn_reason = now, by_ref, why
        row.entity_version += 1
        row.save()
        _audit(by_ref, "privilege_withdrawn", row.public_id)
        return privilege_dict(row, now.date())
