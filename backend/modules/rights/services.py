"""Filing and answering data-rights requests.

A patient (not a guardian: that needs proof of authority this system does not hold) sends a request
to their clinic. Clinic staff can start it and record that they checked who is asking; only a clinic
administrator can close it, and a request that is not about "seeing my data" can only be completed
after the identity check. Every step is audited. The patient's own words are stored sealed.
The clinic does the work itself (a deletion, a correction): this only records and tracks it.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .models import OPEN_STATES, CaseState, RequestType, RightsCase

WRITE_RIGHTS = "write:rights"
STAFF_ROLES = frozenset({"R04", "R13"})       # may start a request and record the identity check
CLOSING_ROLES = frozenset({"R13"})            # may complete or decline it
MAX_NOTE = 500
MAX_DECISION_NOTE = 300
MAX_OPEN_PER_PERSON = 5


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _own(case_ref: str) -> RightsCase:
    found = RightsCase.objects.select_for_update().filter(public_id=case_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def _context(public_id: str) -> str:
    return f"rights:{public_id}:note"


def case_dict(c: RightsCase) -> dict[str, Any]:
    note = ""
    if c.note_enc:
        try:
            note = decrypt(c.note_enc, tenant_id=c.tenant_id, context=_context(c.public_id))
        except CryptoError:
            note = ""
    return {
        "public_id": c.public_id, "ref_no": c.ref_no, "person_ref": c.person_ref,
        "organization_ref": c.organization_ref, "request_type": c.request_type, "note": note,
        "state": c.state, "created_at": c.created_at.isoformat(),
        "identity_checked_at": c.identity_checked_at.isoformat() if c.identity_checked_at else None,
        "decision_note": c.decision_note,
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
        "due_at": c.due_at.isoformat() if c.due_at else None, "due_basis": c.due_basis,
    }


def _clean_note(value: str, limit: int, field: str, low: int = 0) -> str:
    text = " ".join((value or "").split())
    flat = (value or "").replace("\n", " ")
    if len(text) > limit or len(text) < low or any(ord(ch) < 32 for ch in flat):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return text


def file_request(
    *, person_ref: str, organization_ref: str, request_type: str, note: str,
    actor_public_id: str, decision: policy.Decision,
    deadline: tuple[int, str] | None = None, clock: Clock | None = None,
) -> dict[str, Any]:
    """The patient sends a request to their clinic. Only the patient themselves."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_RIGHTS,
        organization_public_id=organization_ref, subject_ref=person_ref)
    if decision.role_code != "R01":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if request_type not in RequestType.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["type"])
    clean = _clean_note(note, MAX_NOTE, "note")
    with transaction.atomic():
        open_ones = RightsCase.objects.filter(
            person_ref=person_ref, state__in=OPEN_STATES, **_scope())
        if open_ones.filter(request_type=request_type, organization_ref=organization_ref).exists():
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)          # that one is already open
        if open_ones.count() >= MAX_OPEN_PER_PERSON:
            raise ZoikoError(ErrorCode.RATE_LIMITED)
        row = RightsCase(
            person_ref=person_ref, organization_ref=organization_ref,
            requested_by_ref=actor_public_id, request_type=request_type, ref_no="", **_scope())
        row.ref_no = f"DSR-{row.public_id[:8].upper()}"
        if deadline is not None:                      # (days, "pack@revision") from a pack
            row.due_at = utc_now(clock) + dt.timedelta(days=deadline[0])
            row.due_basis = deadline[1][:80]
        if clean:
            row.note_enc = encrypt(clean, tenant_id=row.tenant_id, context=_context(row.public_id))
        row.save()
        trust.link_person_to_site(person_ref, organization_ref, "rights")
        record(actor_ref=actor_public_id, action="rights.request", object_type="rights_case",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED, reason_code=request_type)
        emit(event_type="rights.requested.v1", aggregate_type="rights_case",
             aggregate_ref=row.public_id, payload={"case_ref": row.public_id})
        return case_dict(row)


def withdraw(
    *, case_ref: str, actor_public_id: str, decision: policy.Decision
) -> dict[str, Any]:
    """The patient takes their request back. Repeating it is harmless."""
    with transaction.atomic():
        row = _own(case_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_RIGHTS,
            organization_public_id=row.organization_ref, subject_ref=row.person_ref)
        if decision.role_code != "R01" or row.requested_by_ref != actor_public_id:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if row.state == CaseState.WITHDRAWN:
            return case_dict(row)
        if row.state not in OPEN_STATES:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.state = CaseState.WITHDRAWN
        row.entity_version += 1
        row.save(update_fields=["state", "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="rights.withdraw", object_type="rights_case",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED)
        emit(event_type="rights.withdrawn.v1", aggregate_type="rights_case",
             aggregate_ref=row.public_id, payload={"case_ref": row.public_id, "side": "patient"})
        return case_dict(row)


def _staff_step(case_ref: str, actor: str, decision: policy.Decision) -> RightsCase:
    row = _own(case_ref)
    policy.check_receipt(
        decision, actor_public_id=actor, operation=WRITE_RIGHTS,
        organization_public_id=row.organization_ref, subject_ref=row.person_ref)
    if decision.role_code not in STAFF_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if row.state not in OPEN_STATES:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    return row


def start(*, case_ref: str, actor_public_id: str, decision: policy.Decision) -> dict[str, Any]:
    """Staff pick the request up: it is now being looked at."""
    with transaction.atomic():
        row = _staff_step(case_ref, actor_public_id, decision)
        if row.state == CaseState.IN_PROGRESS:
            return case_dict(row)
        row.state = CaseState.IN_PROGRESS
        row.entity_version += 1
        row.save(update_fields=["state", "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="rights.start", object_type="rights_case",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED)
        return case_dict(row)


def confirm_identity(
    *, case_ref: str, actor_public_id: str, decision: policy.Decision, clock: Clock | None = None
) -> dict[str, Any]:
    """Staff record that they checked who is asking (at the desk, as with a guardian). It is a
    recorded fact, not a level of assurance. A request cannot be completed before it."""
    with transaction.atomic():
        row = _staff_step(case_ref, actor_public_id, decision)
        if row.identity_checked_at is None:
            row.identity_checked_by_ref = actor_public_id
            row.identity_checked_at = utc_now(clock)
            row.entity_version += 1
            row.save(update_fields=["identity_checked_by_ref", "identity_checked_at",
                                    "entity_version", "updated_at"])
            record(actor_ref=actor_public_id, action="rights.identity_checked",
                   object_type="rights_case", object_ref=row.public_id,
                   outcome=AuditOutcome.ALLOWED)
        return case_dict(row)


def close(
    *, case_ref: str, outcome: str, note: str, actor_public_id: str, decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """A clinic administrator completes or declines the request, with a note the patient will see.
    Completing needs the identity check first; declining needs a reason."""
    if outcome not in ("completed", "declined"):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["outcome"])
    with transaction.atomic():
        row = _staff_step(case_ref, actor_public_id, decision)
        if decision.role_code not in CLOSING_ROLES:
            raise ZoikoError(ErrorCode.POLICY_DENIED)    # authority is checked before the wording
        clean = _clean_note(note, MAX_DECISION_NOTE, "note", low=3 if outcome == "declined" else 0)
        if outcome == "completed" and row.identity_checked_at is None:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["identity"])
        row.state = CaseState.COMPLETED if outcome == "completed" else CaseState.DECLINED
        row.decided_by_ref = actor_public_id
        row.decision_note = clean
        row.decided_at = utc_now(clock)
        row.entity_version += 1
        row.save(update_fields=["state", "decided_by_ref", "decision_note", "decided_at",
                                "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action=f"rights.{outcome}", object_type="rights_case",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED)
        emit(event_type="rights.closed.v1", aggregate_type="rights_case",
             aggregate_ref=row.public_id,
             payload={"case_ref": row.public_id, "actor_ref": actor_public_id})
        return case_dict(row)


def get_case(case_ref: str) -> dict[str, Any]:
    found = RightsCase.objects.filter(public_id=case_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return case_dict(found)


def list_cases(
    *, person_refs: list[str] | None = None, organization_ref: str = "", limit: int = 200
) -> list[dict[str, Any]]:
    """Newest first. The caller has authorized each patient or organization it asks for."""
    rows = RightsCase.objects.filter(**_scope())
    if person_refs is not None:
        rows = rows.filter(person_ref__in=person_refs)
    if organization_ref:
        rows = rows.filter(organization_ref=organization_ref)
    return [case_dict(c) for c in rows.order_by("-created_at")[: max(1, min(limit, 200))]]


def open_count(organization_ref: str) -> int:
    """How many requests wait for this clinic (a number only; nothing is read)."""
    return RightsCase.objects.filter(
        organization_ref=organization_ref, state__in=OPEN_STATES, **_scope()).count()
