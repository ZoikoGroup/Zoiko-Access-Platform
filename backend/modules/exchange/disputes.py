"""A patient disputes a result: "this is not mine" or "these values look wrong" (DEV-385).

GHE-06 I-05: an ambiguous match or conflicting identity -> DENY / HOLD. GHE-06 W04: the patient
portal shows a restricted match and the "ability to dispute". GHE-07: a wrong-patient delivery is a
zero-tolerance failure.

Rules
- Only for a result that has arrived, for the patient it is about (or someone who may act for them;
  the API checks that), and only with a reason CODE: no free text, so nothing clinical is typed in.
- While a dispute is open the result is HELD: the patient sees no values (services.
  _patient_may_see_values) and it cannot be marked reviewed (services.clinic_step).
- The ordering clinic's doctor decides: CONFIRMED (it is theirs: released again, a later dispute
  is possible) or NOT_THEIRS (withheld from the patient for good; the clinic must report it and
  have the result corrected). NOT_THEIRS needs a note (no patient details) and is final.
- Every step is audited and in the request's history. Call with the platform tenant bound.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction

from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now

from .models import ExchangeRequest
from .services import _audit, _event, _scope, clinic_dict

OPEN, CONFIRMED, NOT_THEIRS = "open", "confirmed", "not_theirs"
REASONS = {
    "not_me": "This result is not mine",
    "wrong_values": "The values look wrong",
    "wrong_test": "I did not have this test",
}
HELD_STATES = (OPEN, NOT_THEIRS)


def _row(request_ref: str, tenant_ref: str) -> ExchangeRequest:
    row = ExchangeRequest.objects.select_for_update().filter(
        public_id=request_ref, from_tenant_ref=tenant_ref, **_scope()).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


def raise_dispute(*, request_ref: str, tenant_ref: str, person_refs: list[str], actor_ref: str,
                  reason: object, clock: Clock | None = None) -> dict[str, Any]:
    if reason not in REASONS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["reason"])
    with transaction.atomic():
        row = _row(request_ref, tenant_ref)
        if row.person_ref not in person_refs:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.result_at is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["no_result"])
        if row.dispute_state in HELD_STATES:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["dispute"])   # already held
        row.dispute_state, row.dispute_reason = OPEN, str(reason)
        row.disputed_at, row.dispute_by_ref = utc_now(clock), actor_ref
        row.dispute_decided_by_ref, row.dispute_decided_at, row.dispute_decision_note = "", None, ""
        row.entity_version += 1
        row.save()
        _event(row, "disputed", "patient", actor_ref)
        _audit(actor_ref, "exchange.result_disputed", row)
        return {"id": row.public_id, "dispute": OPEN, "reason": row.dispute_reason}


def decide_dispute(*, request_ref: str, tenant_ref: str, actor_ref: str, outcome: object,
                   note: object = "", clock: Clock | None = None) -> dict[str, Any]:
    text = note.strip() if isinstance(note, str) else ""
    if outcome not in (CONFIRMED, NOT_THEIRS):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["outcome"])
    if len(text) > 300 or (outcome == NOT_THEIRS and len(text) < 5):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["note"])
    with transaction.atomic():
        row = _row(request_ref, tenant_ref)
        if row.dispute_state != OPEN:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["dispute"])
        row.dispute_state = str(outcome)
        row.dispute_decided_by_ref, row.dispute_decided_at = actor_ref, utc_now(clock)
        row.dispute_decision_note = text
        row.entity_version += 1
        row.save()
        _event(row, f"dispute_{outcome}", "clinic", actor_ref)
        _audit(actor_ref, f"exchange.dispute_{outcome}", row)
        return clinic_dict(row)
