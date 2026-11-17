"""Consultation notes: draft, edit while a draft, sign, then only supersede (DEV-304).

Every function that changes or reads a note needs a policy receipt for the right operation, passed
in by the caller (the caller asks the policy engine about the patient first, which checks the
clinician's care relationship and records the decision). Only the author can edit, sign or amend a
note. The text is encrypted at rest and returned only by `read`, which is itself audited.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import transaction

from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import AmendReason, ClinicalNote, NoteAddendum, NoteState

READ, WRITE, ATTEST = "read:note", "write:note", "attest:note"
MAX_BODY = 20_000
MAX_ADDENDUM = 5_000
PATIENT_DELAY_HOURS = 24  # a patient sees a signed note this long after it was signed (DEV-306)
EARLIEST = dt.date(1900, 1, 1)


def enabled() -> bool:
    return bool(getattr(settings, "CLINICAL_NOTES_ENABLED", False))


def _require_on() -> None:
    if not enabled():
        raise ZoikoError(ErrorCode.NOT_FOUND)


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _audit(actor: str, action: str, note: ClinicalNote, reason: str = "") -> None:
    record(
        actor_ref=actor,
        action=f"clinicalnote.{action}",
        object_type="clinical_note",
        object_ref=note.public_id,
        outcome=AuditOutcome.ALLOWED,
        reason_code=reason,
    )


def _seal(note: ClinicalNote, text: str) -> str:
    return encrypt(
        text, tenant_id=require_context().tenant_id, context=f"note:{note.public_id}:body"
    )


def _open(note: ClinicalNote) -> str:
    try:
        return decrypt(
            note.body_enc,
            tenant_id=require_context().tenant_id,
            context=f"note:{note.public_id}:body",
        )
    except CryptoError:
        return ""  # unreadable (wrong key or tampered): show nothing, not junk


def meta_dict(n: ClinicalNote) -> dict[str, Any]:
    """Everything about a note except its text."""
    return {
        "id": n.public_id,
        "person": n.person_ref,
        "organization": n.organization_ref,
        "author": n.author_ref,
        "note_date": n.note_date.isoformat(),
        "state": n.state,
        "version": n.version,
        "supersedes": n.supersedes_ref or None,
        "superseded_by": n.superseded_by_ref or None,
        "amend_reason": n.amend_reason or None,
        "signed_by": n.signed_by_ref or None,
        "signed_at": n.signed_at.isoformat() if n.signed_at else None,
        "created_at": n.created_at.isoformat(),
    }


def _clean_body(value: Any) -> str:
    if not isinstance(value, str):
        raise _bad("body")
    text = value.replace("\r\n", "\n").strip()
    if not 1 <= len(text) <= MAX_BODY or "\x00" in text:
        raise _bad("body")
    return text


def _clean_date(value: Any, clock: Clock | None) -> dt.date:
    if not isinstance(value, str) or len(value) != 10:
        raise _bad("note_date")
    try:
        day = dt.date.fromisoformat(value)
    except ValueError:
        raise _bad("note_date") from None
    if not EARLIEST <= day <= utc_now(clock).date() + dt.timedelta(days=1):
        raise _bad("note_date")
    return day


def _mine(note_ref: str) -> ClinicalNote:
    _require_on()
    note = ClinicalNote.objects.select_for_update().filter(public_id=note_ref, **_scope()).first()
    if note is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return note


def get(note_ref: str) -> dict[str, Any]:
    """Which patient and clinic a note belongs to and who wrote it, so the caller can ask the
    policy engine before reading or changing it. NOT_FOUND when the feature is off."""
    _require_on()
    found = ClinicalNote.objects.filter(public_id=note_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return {
        "id": found.public_id,
        "person": found.person_ref,
        "organization": found.organization_ref,
        "author": found.author_ref,
        "state": found.state,
    }


def create_draft(
    *,
    person_ref: str,
    organization_ref: str,
    body: Any,
    note_date: Any,
    by: str,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    _require_on()
    policy.check_receipt(
        decision,
        actor_public_id=by,
        operation=WRITE,
        organization_public_id=organization_ref,
        subject_ref=person_ref,
    )
    text, day = _clean_body(body), _clean_date(note_date, clock)
    with transaction.atomic():
        note = ClinicalNote(
            person_ref=person_ref,
            organization_ref=organization_ref,
            author_ref=by,
            note_date=day,
            body_enc="",
            **_scope(),
        )
        note.body_enc = _seal(note, text)
        note.save()
        _audit(by, "draft_created", note)
    return meta_dict(note)


def edit_draft(*, note_ref: str, body: Any, by: str, decision: policy.Decision) -> dict[str, Any]:
    text = _clean_body(body)
    with transaction.atomic():
        note = _mine(note_ref)
        policy.check_receipt(
            decision,
            actor_public_id=by,
            operation=WRITE,
            organization_public_id=note.organization_ref,
            subject_ref=note.person_ref,
        )
        if note.author_ref != by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if note.state != NoteState.DRAFT:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # a signed note is never edited
        note.body_enc = _seal(note, text)
        note.entity_version += 1
        note.save(update_fields=["body_enc", "entity_version", "updated_at"])
        _audit(by, "draft_edited", note)
        return meta_dict(note)


def sign(
    *, note_ref: str, by: str, decision: policy.Decision, clock: Clock | None = None
) -> dict[str, Any]:
    """The author signs a draft. From then on it cannot change. If it replaces an earlier version,
    that one becomes 'replaced' in the same step."""
    with transaction.atomic():
        note = _mine(note_ref)
        policy.check_receipt(
            decision,
            actor_public_id=by,
            operation=ATTEST,
            organization_public_id=note.organization_ref,
            subject_ref=note.person_ref,
        )
        if note.author_ref != by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if note.state == NoteState.SIGNED:
            return meta_dict(note)  # repeating it is harmless
        if note.state != NoteState.DRAFT:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        note.state, note.signed_by_ref, note.signed_at = NoteState.SIGNED, by, utc_now(clock)
        note.entity_version += 1
        note.save(
            update_fields=["state", "signed_by_ref", "signed_at", "entity_version", "updated_at"]
        )
        if note.supersedes_ref:
            old = (
                ClinicalNote.objects.select_for_update()
                .filter(public_id=note.supersedes_ref, **_scope())
                .first()
            )
            if old is not None and old.state == NoteState.SIGNED:
                old.state, old.superseded_by_ref = NoteState.SUPERSEDED, note.public_id
                old.entity_version += 1
                old.save(
                    update_fields=["state", "superseded_by_ref", "entity_version", "updated_at"]
                )
                _audit(by, "superseded", old)
        _audit(by, "signed", note)
        return meta_dict(note)


def amend(*, note_ref: str, reason: Any, by: str, decision: policy.Decision) -> dict[str, Any]:
    """Start a correction of a signed note: a new draft, one version higher, with the old text
    copied in. The old note stays exactly as signed until the new one is signed."""
    if reason not in AmendReason.values:
        raise _bad("reason")
    with transaction.atomic():
        old = _mine(note_ref)
        policy.check_receipt(
            decision,
            actor_public_id=by,
            operation=WRITE,
            organization_public_id=old.organization_ref,
            subject_ref=old.person_ref,
        )
        if old.author_ref != by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if old.state != NoteState.SIGNED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if ClinicalNote.objects.filter(
            supersedes_ref=old.public_id, state=NoteState.DRAFT, **_scope()
        ).exists():
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # a correction is already open
        new = ClinicalNote(
            person_ref=old.person_ref,
            organization_ref=old.organization_ref,
            author_ref=by,
            note_date=old.note_date,
            body_enc="",
            version=old.version + 1,
            supersedes_ref=old.public_id,
            amend_reason=reason,
            **_scope(),
        )
        new.body_enc = _seal(new, _open(old))
        new.save()
        _audit(by, "amendment_started", new, reason)
        return meta_dict(new)


def _addenda(note: ClinicalNote, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """The addenda on a note, oldest first. For a patient (`now` given) only those past the same
    delay as the note itself."""
    rows = NoteAddendum.objects.filter(note=note, **_scope()).order_by("created_at")
    out = []
    for a in rows:
        if now is not None and a.created_at + dt.timedelta(hours=PATIENT_DELAY_HOURS) > now:
            continue
        try:
            text = decrypt(
                a.body_enc,
                tenant_id=require_context().tenant_id,
                context=f"note:{a.public_id}:addendum",
            )
        except CryptoError:
            text = ""
        out.append(
            {
                "id": a.public_id,
                "author": a.author_ref,
                "added_at": a.created_at.isoformat(),
                "body": text,
            }
        )
    return out


def add_addendum(*, note_ref: str, body: Any, by: str, decision: policy.Decision) -> dict[str, Any]:
    """The author adds a short dated text to a signed note. The note itself is not touched."""
    if not isinstance(body, str):
        raise _bad("body")
    text = body.replace("\r\n", "\n").strip()
    if not 1 <= len(text) <= MAX_ADDENDUM or "\x00" in text:
        raise _bad("body")
    with transaction.atomic():
        note = _mine(note_ref)
        policy.check_receipt(
            decision,
            actor_public_id=by,
            operation=WRITE,
            organization_public_id=note.organization_ref,
            subject_ref=note.person_ref,
        )
        if note.author_ref != by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if note.state != NoteState.SIGNED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # only the current signed version
        added = NoteAddendum(note=note, author_ref=by, body_enc="", **_scope())
        added.body_enc = encrypt(
            text, tenant_id=require_context().tenant_id, context=f"note:{added.public_id}:addendum"
        )
        added.save()
        _audit(by, "addendum_added", note)
        return {**meta_dict(note), "addenda": _addenda(note)}


def read(*, note_ref: str, by: str, decision: policy.Decision) -> dict[str, Any]:
    """The note with its text. Every read is recorded."""
    _require_on()
    note = ClinicalNote.objects.filter(public_id=note_ref, **_scope()).first()
    if note is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    policy.check_receipt(
        decision,
        actor_public_id=by,
        operation=READ,
        organization_public_id=note.organization_ref,
        subject_ref=note.person_ref,
    )
    _audit(by, "read", note)
    return {**meta_dict(note), "body": _open(note), "addenda": _addenda(note)}


def list_for_person(
    *, person_ref: str, by: str, decision: policy.Decision, organization_ref: str | None = None
) -> list[dict[str, Any]]:
    """A patient's notes, newest first, WITHOUT their text. The caller asked the policy engine
    about this patient first. A draft is shown only to its own author."""
    _require_on()
    policy.check_receipt(
        decision,
        actor_public_id=by,
        operation=READ,
        organization_public_id=organization_ref,
        subject_ref=person_ref,
    )
    rows = ClinicalNote.objects.filter(person_ref=person_ref, **_scope()).order_by(
        "-note_date", "-version"
    )
    return [meta_dict(n) for n in rows if n.state != NoteState.DRAFT or n.author_ref == by]


def _patient_sees(n: ClinicalNote, now: dt.datetime) -> bool:
    """A patient sees a note only once signed (never a draft), and only after the delay, so the
    doctor can follow up first. A replaced version is shown too, marked as corrected."""
    return (
        n.state in (NoteState.SIGNED, NoteState.SUPERSEDED)
        and n.signed_at is not None
        and n.signed_at + dt.timedelta(hours=PATIENT_DELAY_HOURS) <= now
    )


def _patient_meta(n: ClinicalNote) -> dict[str, Any]:
    return {
        "id": n.public_id,
        "note_date": n.note_date.isoformat(),
        "version": n.version,
        "corrected": n.state == NoteState.SUPERSEDED,
        "amend_reason": n.amend_reason or None,
        "author": n.author_ref,
        "signed_at": n.signed_at.isoformat() if n.signed_at else None,
    }


def patient_list(
    *, person_ref: str, by: str, decision: policy.Decision, clock: Clock | None = None
) -> list[dict[str, Any]]:
    """The patient's own notes that they may see yet, newest first, without the text."""
    _require_on()
    policy.check_receipt(
        decision,
        actor_public_id=by,
        operation=READ,
        organization_public_id=None,
        subject_ref=person_ref,
    )
    now = utc_now(clock)
    rows = ClinicalNote.objects.filter(person_ref=person_ref, **_scope()).order_by(
        "-note_date", "-version"
    )
    return [_patient_meta(n) for n in rows if _patient_sees(n, now)]


def patient_read(
    *,
    note_ref: str,
    person_ref: str,
    by: str,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """One of the patient's own notes, with its text. Not found if it is a draft, still inside the
    delay, or someone else's. Every read is recorded."""
    _require_on()
    note = ClinicalNote.objects.filter(
        public_id=note_ref, person_ref=person_ref, **_scope()
    ).first()
    if note is None or not _patient_sees(note, utc_now(clock)):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    policy.check_receipt(
        decision,
        actor_public_id=by,
        operation=READ,
        organization_public_id=None,
        subject_ref=person_ref,
    )
    _audit(by, "read_by_patient", note)
    return {
        **_patient_meta(note),
        "body": _open(note),
        "addenda": _addenda(note, utc_now(clock)),
    }
