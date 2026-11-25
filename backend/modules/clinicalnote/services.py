"""Consultation notes: draft, edit while a draft, sign, then only supersede (DEV-304).

Every function that changes or reads a note needs a policy receipt for the right operation, passed
in by the caller (the caller asks the policy engine about the patient first, which checks the
clinician's care relationship and records the decision). Only the author can edit, sign or amend a
note. The text is encrypted at rest and returned only by `read`, which is itself audited.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from foundation import pagination
from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import AmendReason, ClinicalNote, NoteAddendum, NoteAttachment, NoteState, ScanState

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
    if not note.body_enc:  # erased after its retention period (DEV-324)
        return ""
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
        "guardian_hidden": n.guardian_hidden,
        "erased": n.erased_at is not None,
        "has_diagnoses": bool(n.diagnoses_enc),
        "has_medications": bool(n.medications_enc),
        "has_attachments": NoteAttachment.objects.filter(note=n, **_scope()).exists(),
    }


MAX_DIAGNOSES = 10
DIAGNOSIS_STATUS = ("confirmed", "provisional", "ruled_out")


def _clean_diagnoses(value: Any) -> list[dict[str, str]]:
    """Up to 10 coded diagnoses: a SNOMED CT concept, its words (up to 120 characters) and a
    status (confirmed, provisional, ruled out). Nothing else, so no free text hides in them."""
    from foundation import fhir

    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > MAX_DIAGNOSES:
        raise _bad("diagnoses")
    out, seen = [], set()
    for d in value:
        if not isinstance(d, dict) or not set(d) <= {"code", "text", "status"}:
            raise _bad("diagnoses")
        code, text = d.get("code"), d.get("text")
        status = d.get("status") or "confirmed"
        if not isinstance(code, str) or not fhir.sctid_ok(code) or code in seen:
            raise _bad("diagnoses")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 120 or any(
                ord(ch) < 32 for ch in text):
            raise _bad("diagnoses")
        if status not in DIAGNOSIS_STATUS:
            raise _bad("diagnoses")
        seen.add(code)
        out.append({"code": code, "text": text.strip(), "status": status})
    return out


def _seal_diagnoses(note: ClinicalNote, items: list[dict[str, str]]) -> str:
    if not items:
        return ""
    import json

    return encrypt(json.dumps(items), tenant_id=require_context().tenant_id,
                   context=f"note:{note.public_id}:diagnoses")


def _open_diagnoses(note: ClinicalNote) -> list[dict[str, str]]:
    if not note.diagnoses_enc:
        return []
    import json

    try:
        found = json.loads(decrypt(note.diagnoses_enc, tenant_id=require_context().tenant_id,
                                   context=f"note:{note.public_id}:diagnoses"))
    except (CryptoError, ValueError):
        return []
    return found if isinstance(found, list) else []


MAX_MEDICATIONS = 10
MEDICATION_STATUS = ("active", "suspended", "stopped")


def _clean_medications(value: Any) -> list[dict[str, str]]:
    """Up to 10 structured medications (DEV-375): a SNOMED CT / dm+d concept, a name
    (up to 120 characters), dose/instructions (up to 120 characters) and a status
    (active, suspended, stopped). Nothing else, so no free text hides in them."""
    from foundation import fhir

    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > MAX_MEDICATIONS:
        raise _bad("medications")
    out, seen = [], set()
    for m in value:
        if not isinstance(m, dict) or not set(m) <= {"code", "name", "dose", "status"}:
            raise _bad("medications")
        code = m.get("code")
        name = m.get("name")
        dose = m.get("dose") or ""
        status = m.get("status") or "active"
        if not isinstance(code, str) or not fhir.sctid_ok(code) or code in seen:
            raise _bad("medications")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(
                ord(ch) < 32 for ch in name):
            raise _bad("medications")
        if not isinstance(dose, str) or len(dose.strip()) > 120 or any(
                ord(ch) < 32 for ch in dose):
            raise _bad("medications")
        if status not in MEDICATION_STATUS:
            raise _bad("medications")
        seen.add(code)
        out.append({"code": code, "name": name.strip(), "dose": dose.strip(), "status": status})
    return out


def _seal_medications(note: ClinicalNote, items: list[dict[str, str]]) -> str:
    if not items:
        return ""
    import json

    return encrypt(json.dumps(items), tenant_id=require_context().tenant_id,
                   context=f"note:{note.public_id}:medications")


def _open_medications(note: ClinicalNote) -> list[dict[str, str]]:
    if not note.medications_enc:
        return []
    import json

    try:
        found = json.loads(decrypt(note.medications_enc, tenant_id=require_context().tenant_id,
                                   context=f"note:{note.public_id}:medications"))
    except (CryptoError, ValueError):
        return []
    return found if isinstance(found, list) else []


MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 5
_ACTIVE_PDF = re.compile(
    rb"/(JavaScript|JS|Launch|EmbeddedFile|OpenAction|AA|RichMedia|XFA)(?![A-Za-z0-9])"
)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()-]")


def _attach_context(note: ClinicalNote, a: NoteAttachment) -> str:
    return f"note:{note.public_id}:attachment:{a.public_id}"


def attachment_dict(a: NoteAttachment) -> dict[str, Any]:
    return {
        "id": a.public_id,
        "filename": a.filename,
        "content_type": a.content_type,
        "size": a.size,
        "scan": a.scan_state,
        "uploaded_at": a.created_at.isoformat(),
    }


def _apply_scan(a: NoteAttachment, data: bytes, clock: Clock | None = None) -> None:
    from foundation import scanner

    verdict = scanner.scan(data)
    if verdict == "pending":
        return
    a.scan_state = ScanState.CLEAN if verdict == "clean" else ScanState.INFECTED
    a.scanned_at = utc_now(clock)
    if a.scan_state == ScanState.INFECTED:
        a.content_enc = ""  # never kept, never opened
    a.entity_version += 1
    a.save(
        update_fields=[
            "scan_state",
            "scanned_at",
            "content_enc",
            "entity_version",
            "updated_at",
        ]
    )
    _audit("system", f"attachment_{a.scan_state}", a.note)


def _detect_type(data: bytes, filename: str) -> tuple[str, str]:
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_ATTACHMENT_BYTES:
        raise _bad("file")
    if data.startswith(b"%PDF-") and b"%%EOF" in data[-2048:]:
        if _ACTIVE_PDF.search(data):
            raise _bad("file")
        ctype = "application/pdf"
        ext = ".pdf"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        ctype = "image/png"
        ext = ".png"
    elif data.startswith(b"\xff\xd8\xff"):
        ctype = "image/jpeg"
        ext = ".jpg"
    else:
        raise _bad("file")

    name = (
        _SAFE_NAME.sub("_", filename.strip())[:100]
        if isinstance(filename, str) and filename.strip()
        else ""
    )
    if not name:
        name = f"attachment{ext}"
    elif not name.lower().endswith(ext):
        name = f"{name[:96]}{ext}"
    return ctype, name


def _attachments(note: ClinicalNote, for_patient: bool = False) -> list[dict[str, Any]]:
    rows = NoteAttachment.objects.filter(note=note, **_scope()).order_by("created_at")
    if for_patient:
        return [attachment_dict(a) for a in rows if a.scan_state == ScanState.CLEAN]
    return [attachment_dict(a) for a in rows]


def add_attachment(
    *,
    note_ref: str,
    filename: str,
    data: bytes,
    by: str,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Attach a PDF or image to a draft note (DEV-376). Refused once signed."""
    _require_on()
    import base64
    import hashlib

    ctype, name = _detect_type(data, filename)
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
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if NoteAttachment.objects.filter(note=note, **_scope()).count() >= MAX_ATTACHMENTS:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        a = NoteAttachment(
            note=note,
            filename=name,
            content_type=ctype,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_enc="",
            uploaded_by_ref=by,
            **_scope(),
        )
        a.content_enc = encrypt(
            base64.b64encode(data).decode("ascii"),
            tenant_id=require_context().tenant_id,
            context=_attach_context(note, a),
        )
        a.save()
        _audit(by, "attachment_added", note)
        _apply_scan(a, data, clock)
        return attachment_dict(a)


def remove_attachment(
    *, note_ref: str, attachment_ref: str, by: str, decision: policy.Decision
) -> None:
    """Remove an attachment from a draft note."""
    _require_on()
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
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        a = NoteAttachment.objects.filter(
            public_id=attachment_ref, note=note, **_scope()
        ).first()
        if a is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        a.delete()
        _audit(by, "attachment_removed", note)


def open_attachment(
    *, note_ref: str, attachment_ref: str, by: str, decision: policy.Decision
) -> tuple[str, str, bytes]:
    """Open an attachment on a note for a clinician caring for this patient."""
    _require_on()
    import base64

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
    a = NoteAttachment.objects.filter(
        public_id=attachment_ref, note=note, **_scope()
    ).first()
    if a is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if a.scan_state != ScanState.CLEAN:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    try:
        data = base64.b64decode(
            decrypt(
                a.content_enc,
                tenant_id=require_context().tenant_id,
                context=_attach_context(note, a),
            )
        )
    except (CryptoError, ValueError):
        raise ZoikoError(ErrorCode.NOT_FOUND) from None
    _audit(by, "attachment_opened", note)
    return a.filename, a.content_type, data


def patient_open_attachment(
    *,
    note_ref: str,
    attachment_ref: str,
    person_ref: str,
    by: str,
    decision: policy.Decision,
    clock: Clock | None = None,
    as_guardian: bool = False,
) -> tuple[str, str, bytes]:
    """Open an attachment on a note for the patient (or guardian), after the 24-hour embargo."""
    _require_on()
    import base64

    note = ClinicalNote.objects.filter(
        public_id=note_ref, person_ref=person_ref, **_scope()
    ).first()
    if note is None or not _patient_sees(note, utc_now(clock), as_guardian):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    policy.check_receipt(
        decision,
        actor_public_id=by,
        operation=READ,
        organization_public_id=None,
        subject_ref=person_ref,
    )
    a = NoteAttachment.objects.filter(
        public_id=attachment_ref, note=note, **_scope()
    ).first()
    if a is None or a.scan_state != ScanState.CLEAN:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    try:
        data = base64.b64decode(
            decrypt(
                a.content_enc,
                tenant_id=require_context().tenant_id,
                context=_attach_context(note, a),
            )
        )
    except (CryptoError, ValueError):
        raise ZoikoError(ErrorCode.NOT_FOUND) from None
    _audit(
        by,
        "attachment_opened_by_guardian" if as_guardian else "attachment_opened_by_patient",
        note,
    )
    return a.filename, a.content_type, data


def scan_pending_attachments(limit: int = 20, clock: Clock | None = None) -> int:
    import base64

    done = 0
    for a in (
        NoteAttachment.objects.select_related("note")
        .filter(scan_state=ScanState.PENDING, **_scope())
        .order_by("created_at")[:limit]
    ):
        try:
            data = base64.b64decode(
                decrypt(
                    a.content_enc,
                    tenant_id=require_context().tenant_id,
                    context=_attach_context(a.note, a),
                )
            )
        except (CryptoError, ValueError):
            continue
        with transaction.atomic():
            _apply_scan(a, data, clock)
        done += a.scan_state != ScanState.PENDING
    return done


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
    diagnoses: Any = None,
    medications: Any = None,
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
    coded = _clean_diagnoses(diagnoses)
    meds = _clean_medications(medications)
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
        note.diagnoses_enc = _seal_diagnoses(note, coded)
        note.medications_enc = _seal_medications(note, meds)
        note.save()
        _audit(by, "draft_created", note)
    return meta_dict(note)


def edit_draft(
    *,
    note_ref: str,
    body: Any,
    by: str,
    decision: policy.Decision,
    diagnoses: Any = None,
    medications: Any = None,
) -> dict[str, Any]:
    """Change a draft's text and, when given, its diagnoses or medications (None keeps them as they are)."""
    text = _clean_body(body)
    coded = None if diagnoses is None else _clean_diagnoses(diagnoses)
    meds = None if medications is None else _clean_medications(medications)
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
        update_fields = ["body_enc", "entity_version", "updated_at"]
        if coded is not None:
            note.diagnoses_enc = _seal_diagnoses(note, coded)
            update_fields.append("diagnoses_enc")
        if meds is not None:
            note.medications_enc = _seal_medications(note, meds)
            update_fields.append("medications_enc")
        note.entity_version += 1
        note.save(update_fields=update_fields)
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
        new.diagnoses_enc = _seal_diagnoses(new, _open_diagnoses(old))
        new.medications_enc = _seal_medications(new, _open_medications(old))
        new.save()
        import base64
        for old_a in NoteAttachment.objects.filter(note=old, scan_state=ScanState.CLEAN, **_scope()):
            try:
                old_data = base64.b64decode(
                    decrypt(
                        old_a.content_enc,
                        tenant_id=require_context().tenant_id,
                        context=_attach_context(old, old_a),
                    )
                )
            except (CryptoError, ValueError):
                continue
            new_a = NoteAttachment(
                note=new,
                filename=old_a.filename,
                content_type=old_a.content_type,
                size=old_a.size,
                sha256=old_a.sha256,
                content_enc="",
                scan_state=ScanState.CLEAN,
                scanned_at=old_a.scanned_at,
                uploaded_by_ref=by,
                **_scope(),
            )
            new_a.content_enc = encrypt(
                base64.b64encode(old_data).decode("ascii"),
                tenant_id=require_context().tenant_id,
                context=_attach_context(new, new_a),
            )
            new_a.save()
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
    return {
        **meta_dict(note),
        "body": _open(note),
        "addenda": _addenda(note),
        "diagnoses": _open_diagnoses(note),
        "medications": _open_medications(note),
        "attachments": _attachments(note, for_patient=False),
    }


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


def page_for_person(
    *,
    person_ref: str,
    by: str,
    decision: policy.Decision,
    limit: int,
    cursor: str | None,
    organization_ref: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of a patient's notes, newest written first, without their text (DEV-320). The same
    rule as the full list: another clinician's draft is never shown."""
    _require_on()
    policy.check_receipt(
        decision,
        actor_public_id=by,
        operation=READ,
        organization_public_id=organization_ref,
        subject_ref=person_ref,
    )
    rows = ClinicalNote.objects.filter(person_ref=person_ref, **_scope()).filter(
        ~Q(state=NoteState.DRAFT) | Q(author_ref=by)
    )
    found, nxt = pagination.page(
        rows, limit=limit, cursor=cursor, actor=by, lst=f"notes:{person_ref}"
    )
    return [meta_dict(n) for n in found], nxt


def _patient_sees(n: ClinicalNote, now: dt.datetime, as_guardian: bool = False) -> bool:
    """A patient sees a note only once signed (never a draft), and only after the delay, so the
    doctor can follow up first. A replaced version is shown too, marked as corrected. A guardian
    never sees a note its author kept from guardians (DEV-324)."""
    return (
        n.state in (NoteState.SIGNED, NoteState.SUPERSEDED)
        and n.signed_at is not None
        and n.signed_at + dt.timedelta(hours=PATIENT_DELAY_HOURS) <= now
        and not (as_guardian and n.guardian_hidden)
    )


# ------------------------------------------------------------------ guardians (DEV-324)
GUARDIAN_UNTIL_AGE = 16  # a guardian reads a child's notes only while the child is under 16


def _age_on(birth: dt.date, day: dt.date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def _birth(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def guardian_may_read(birth_date: str | None, clock: Clock | None = None) -> bool:
    """Whether a guardian may read this child's notes today: only while the child is under 16.
    From 16 the young person decides for themselves. No known birth date: no (fail closed)."""
    birth = _birth(birth_date)
    return birth is not None and _age_on(birth, utc_now(clock).date()) < GUARDIAN_UNTIL_AGE


def set_guardian_hidden(
    *, note_ref: str, hidden: Any, by: str, decision: policy.Decision
) -> dict[str, Any]:
    """The author keeps a note from a guardian, or shows it again. The patient still sees it."""
    if not isinstance(hidden, bool):
        raise _bad("hidden")
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
        if note.guardian_hidden != hidden:
            note.guardian_hidden = hidden
            note.entity_version += 1
            note.save(update_fields=["guardian_hidden", "entity_version", "updated_at"])
            _audit(by, "guardian_hidden" if hidden else "guardian_shown", note)
        return meta_dict(note)


# ------------------------------------------------------------------ retention (DEV-324)
ADULT_YEARS = 8  # NHS Records Management Code: adults, 8 years after the last care
CHILD_UNTIL = 25  # children: until their 25th birthday ...
CHILD_UNTIL_IF_17 = 26  # ... or 26th if they were 17 at the last care


def _plus_years(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February in a year that has none
        return day.replace(year=day.year + years, day=28)


def keep_until(
    last_care: dt.date, birth_date: str | None, died_on: str | None = None
) -> dt.date | None:
    """The first day a patient's notes may be erased under the NHS rules, or None when it cannot
    be worked out safely (no birth date known: a child's notes must never go early). For a patient
    recorded as having died: 8 years after death (DEV-333)."""
    death = _birth(died_on)
    if death is not None:
        return max(_plus_years(death, ADULT_YEARS), _plus_years(last_care, ADULT_YEARS))
    birth = _birth(birth_date)
    if birth is None:
        return None
    adult = _plus_years(last_care, ADULT_YEARS)
    age = _age_on(birth, last_care)
    if age >= 18:
        return adult
    child = _plus_years(birth, CHILD_UNTIL_IF_17 if age == 17 else CHILD_UNTIL)
    return max(adult, child)


def people_with_notes() -> list[str]:
    """Patients in this space who still have note text, for the retention job."""
    return sorted(
        set(
            ClinicalNote.objects.filter(erased_at__isnull=True, **_scope()).values_list(
                "person_ref", flat=True
            )
        )
    )


def erase_expired(
    birth_dates: dict[str, str],
    clock: Clock | None = None,
    death_dates: dict[str, str] | None = None,
) -> int:
    """Erase the text of every note (and its addenda) of each patient whose retention period has
    run out, counted from their LAST note. The rows stay. Returns how many notes were erased."""
    today, now = utc_now(clock).date(), utc_now(clock)
    erased = 0
    for person in people_with_notes():
        rows = ClinicalNote.objects.filter(person_ref=person, **_scope())
        last = max((n.note_date for n in rows), default=None)
        until = (
            keep_until(last, birth_dates.get(person), (death_dates or {}).get(person))
            if last else None
        )
        if until is None or today < until:
            continue
        with transaction.atomic():
            for note in rows.select_for_update().filter(erased_at__isnull=True):
                NoteAddendum.objects.filter(note=note, **_scope()).update(body_enc="")
                NoteAttachment.objects.filter(note=note, **_scope()).update(content_enc="")
                note.body_enc, note.diagnoses_enc, note.medications_enc, note.erased_at = "", "", "", now
                note.entity_version += 1
                note.save(update_fields=["body_enc", "diagnoses_enc", "medications_enc", "erased_at",
                                         "entity_version", "updated_at"])
                _audit("system", "erased_retention", note)
                erased += 1
    return erased


def _patient_meta(n: ClinicalNote) -> dict[str, Any]:
    return {
        "id": n.public_id,
        "note_date": n.note_date.isoformat(),
        "version": n.version,
        "corrected": n.state == NoteState.SUPERSEDED,
        "amend_reason": n.amend_reason or None,
        "author": n.author_ref,
        "signed_at": n.signed_at.isoformat() if n.signed_at else None,
        "erased": n.erased_at is not None,
    }


def patient_list(
    *,
    person_ref: str,
    by: str,
    decision: policy.Decision,
    clock: Clock | None = None,
    as_guardian: bool = False,
) -> list[dict[str, Any]]:
    """The patient's own notes that they may see yet, newest first, without the text. For a
    guardian (`as_guardian`) the caller has checked the child is under 16; notes kept from
    guardians are left out."""
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
    return [_patient_meta(n) for n in rows if _patient_sees(n, now, as_guardian)]


def patient_read(
    *,
    note_ref: str,
    person_ref: str,
    by: str,
    decision: policy.Decision,
    clock: Clock | None = None,
    as_guardian: bool = False,
) -> dict[str, Any]:
    """One of the patient's own notes, with its text. Not found if it is a draft, still inside the
    delay, someone else's, or (for a guardian) kept from guardians. Every read is recorded."""
    _require_on()
    note = ClinicalNote.objects.filter(
        public_id=note_ref, person_ref=person_ref, **_scope()
    ).first()
    if note is None or not _patient_sees(note, utc_now(clock), as_guardian):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    policy.check_receipt(
        decision,
        actor_public_id=by,
        operation=READ,
        organization_public_id=None,
        subject_ref=person_ref,
    )
    _audit(by, "read_by_guardian" if as_guardian else "read_by_patient", note)
    return {
        **_patient_meta(note),
        "body": _open(note),
        "addenda": _addenda(note, utc_now(clock)),
        "diagnoses": _open_diagnoses(note),
        "medications": _open_medications(note),
        "attachments": _attachments(note, for_patient=True),
    }
