"""Adding, withdrawing and listing clinical references (DEV-041, decision 3).

The whole feature is off unless the setting CLINICAL_REFERENCES_ENABLED is True; while it is off
every function here refuses with NOT_FOUND, the same answer the endpoints give.

Who may act is decided by the policy engine in the caller, and proved here: adding or withdrawing
needs an allowed `attest:diagnostic` decision for exactly this clinician and patient (a clinician,
R03, with a verified care relationship, acting for direct care). What is stored is checked so it
cannot hold clinical content: the system is a short code, the record id has no spaces and must
contain a digit, the kind and the withdrawal reason are fixed choices, and the date is a date.
Every add, withdrawal and read leaves an audit entry. Nothing is ever deleted.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import ClinicalReference, RecordKind, ReferenceStatus, WithdrawReason

OPERATION = "attest:diagnostic"
SYSTEM = re.compile(r"^[a-z0-9_-]{2,40}$")
EXTERNAL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
EARLIEST = dt.date(1900, 1, 1)


def enabled() -> bool:
    return bool(getattr(settings, "CLINICAL_REFERENCES_ENABLED", False))


def _require_on() -> None:
    if not enabled():
        raise ZoikoError(ErrorCode.NOT_FOUND)


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _audit(actor: str, action: str, object_type: str, object_ref: str, reason: str = "") -> None:
    record(actor_ref=actor, action=f"clinicalref.{action}", object_type=object_type,
           object_ref=object_ref, outcome=AuditOutcome.ALLOWED, reason_code=reason)


def reference_dict(r: ClinicalReference, *, care_team: bool) -> dict[str, Any]:
    """What a reference shows. The patient sees what kind of record, its date, which clinic and
    which system holds it; the record's id in that system, who added the pointer and why it was
    withdrawn are for the care team only."""
    out: dict[str, Any] = {
        "id": r.public_id, "person": r.person_ref, "organization": r.organization_ref,
        "system": r.system, "kind": r.kind, "refers_to": r.refers_to.isoformat(),
        "status": r.status, "recorded_at": r.created_at.isoformat(),
        "withdrawn_at": r.withdrawn_at.isoformat() if r.withdrawn_at else None,
    }
    if care_team:
        out.update({"external_id": r.external_id, "author": r.author_ref,
                    "withdrawn_reason": r.withdrawn_reason or None,
                    "withdrawn_by": r.withdrawn_by_ref or None})
    return out


def _clean_system(value: Any) -> str:
    if not isinstance(value, str) or not SYSTEM.fullmatch(value):
        raise _bad("system")
    return value


def _clean_external_id(value: Any) -> str:
    """An opaque id from the clinic's own system. No spaces, a narrow set of characters, and at
    least one digit, so a sentence or a plain word ("diabetes") cannot be stored as an id."""
    if (not isinstance(value, str) or not EXTERNAL_ID.fullmatch(value)
            or not any(ch.isdigit() for ch in value)):
        raise _bad("external_id")
    return value


def _clean_date(value: Any, clock: Clock | None) -> dt.date:
    if not isinstance(value, str) or len(value) != 10:
        raise _bad("refers_to")
    try:
        day = dt.date.fromisoformat(value)
    except ValueError:
        raise _bad("refers_to") from None
    # One day of slack: "today" at the clinic may already be tomorrow in UTC.
    if not EARLIEST <= day <= utc_now(clock).date() + dt.timedelta(days=1):
        raise _bad("refers_to")
    return day


def add(*, person_ref: str, organization_ref: str, system: Any, external_id: Any, kind: Any,
        refers_to: Any, by: str, decision: policy.Decision,
        clock: Clock | None = None) -> dict[str, Any]:
    """A treating clinician records that a record exists in their clinic's own system."""
    _require_on()
    policy.check_receipt(decision, actor_public_id=by, operation=OPERATION,
                         organization_public_id=organization_ref, subject_ref=person_ref)
    clean_system = _clean_system(system)
    clean_id = _clean_external_id(external_id)
    if kind not in RecordKind.values:
        raise _bad("kind")
    day = _clean_date(refers_to, clock)
    ref = ClinicalReference(
        person_ref=person_ref, organization_ref=organization_ref, author_ref=by,
        system=clean_system, external_id=clean_id, kind=kind, refers_to=day, **_scope())
    try:
        with transaction.atomic():
            ref.save()
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None   # already pointed at, and active
    _audit(by, "added", "clinical_reference", ref.public_id, kind)
    return reference_dict(ref, care_team=True)


def get(reference_ref: str) -> dict[str, Any]:
    """Which patient and clinic a reference belongs to, so the caller can ask the policy engine
    before withdrawing it. NOT_FOUND when the feature is off or there is no such reference."""
    _require_on()
    found = ClinicalReference.objects.filter(public_id=reference_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return {"id": found.public_id, "person": found.person_ref,
            "organization": found.organization_ref, "status": found.status}


def withdraw(*, reference_ref: str, reason: Any, by: str, decision: policy.Decision,
             clock: Clock | None = None) -> dict[str, Any]:
    """Mark a reference withdrawn. The row stays, so the history of what was pointed at is kept."""
    _require_on()
    if reason not in WithdrawReason.values or reason == WithdrawReason.RETENTION_PERIOD:
        raise _bad("reason")                       # erasing is retention's own step
    with transaction.atomic():
        ref = ClinicalReference.objects.select_for_update().filter(
            public_id=reference_ref, **_scope()).first()
        if ref is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        policy.check_receipt(decision, actor_public_id=by, operation=OPERATION,
                             organization_public_id=ref.organization_ref,
                             subject_ref=ref.person_ref)
        if ref.status != ReferenceStatus.ACTIVE:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        ref.status, ref.withdrawn_reason = ReferenceStatus.WITHDRAWN, reason
        ref.withdrawn_by_ref, ref.withdrawn_at = by, utc_now(clock)
        ref.entity_version += 1
        ref.save(update_fields=["status", "withdrawn_reason", "withdrawn_by_ref", "withdrawn_at",
                                "entity_version", "updated_at"])
        _audit(by, "withdrawn", "clinical_reference", ref.public_id, reason)
        return reference_dict(ref, care_team=True)


def list_for_person(person_ref: str, *, viewer_ref: str, care_team: bool) -> list[dict[str, Any]]:
    """Every reference for one patient, newest first, withdrawn ones included and marked. The
    caller has already been allowed to read this patient. The read itself is audited."""
    _require_on()
    rows = ClinicalReference.objects.filter(person_ref=person_ref, **_scope()).order_by(
        "-refers_to", "-created_at")
    out = [reference_dict(r, care_team=care_team) for r in rows]
    _audit(viewer_ref, "read_care_team" if care_team else "read_own", "person", person_ref)
    return out
