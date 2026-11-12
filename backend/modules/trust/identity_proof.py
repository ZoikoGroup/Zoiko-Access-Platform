"""Identity checks: someone at a clinic confirms who a patient is (GHE-06 W02, section 03).

Verified is not something a patient can claim, and a verified LOGIN is not a verified PERSON: the
only way a patient becomes verified is that a member of clinic staff records a passed check
against an official document. What is stored is THAT the check was made, by whom, where and
when, never the document or its number.

A check is tied to the name and birth date it was made against. If either changes afterwards the
check no longer counts (the patient is unverified again), so "verified" always means "verified as
this person with these details". A check expires, can be withdrawn, and cannot be made while the
patient's record is under a possible-duplicate review (an ambiguous identity is held: GHE-06 I-05).

The three-year validity and the list of documents are our own proposed defaults (DEV-129).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.crypto import blind_index
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now

from .identity_match import _plain, name_tokens, open_case_people
from .models import (
    EvidenceType,
    IdentityStatus,
    Person,
    ProofingOutcome,
    ProofingRecord,
    ProxyGrant,
)
from .services import _audit, _own

DEFAULT_VALID_DAYS = 1095               # three years: our own number, for the lead to confirm
MAX_VALID_DAYS = 3650
WITHDRAW_REASONS = ("details_changed", "mistake", "fraud_concern", "document_expired", "other")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _details_digest(person: Person) -> str:
    plain = _plain(person)
    ctx = require_context()
    text = " ".join(sorted(name_tokens(plain["name"]))) + "|" + plain["birth_date"]
    return blind_index(text, tenant_id=ctx.tenant_id, context="identity:details")


def _counts(record: ProofingRecord, digest: str, now: dt.datetime) -> bool:
    """Does this check still make the patient verified?"""
    return (record.outcome == ProofingOutcome.PASSED and record.withdrawn_at is None
            and (record.valid_until is None or record.valid_until > now)
            and record.details_digest == digest)


def _latest_valid(person: Person, now: dt.datetime) -> ProofingRecord | None:
    digest = _details_digest(person)
    for record in ProofingRecord.objects.filter(person=person, **_scope()).order_by("-checked_at"):
        if _counts(record, digest, now):
            return record
    return None


def refresh_status(
    person: Person, *, clock: Clock | None = None, announce: bool = True
) -> str:
    """Make the stored status say what is true now (a check expired, was withdrawn, or the details
    it was made against changed). Safe to call any time. A patient who stops being verified is
    announced (`announce`); the call that records a check announces that itself."""
    now = utc_now(clock)
    wanted = (IdentityStatus.VERIFIED if _latest_valid(person, now) is not None
              else IdentityStatus.UNVERIFIED)
    if person.identity_status != wanted:
        person.identity_status = wanted
        person.entity_version += 1
        person.save(update_fields=["identity_status", "entity_version", "updated_at"])
        _audit("system", "identity.status_changed", "person", person.public_id, status=str(wanted))
        if announce and wanted == IdentityStatus.UNVERIFIED:
            emit(event_type="identity.subject.proofed.v1", aggregate_type="person",
                 aggregate_ref=person.public_id,
                 payload={"person_ref": person.public_id, "outcome": "lapsed",
                          "status": str(wanted)},
                 dedupe_key=f"proof:{person.public_id}:{person.entity_version}")
    return str(wanted)


def _is_party(person: Person, actor_public_id: str) -> bool:
    """The patient, or someone acting for them, may not vouch for their own identity."""
    own = person.linked_actor.public_id if person.linked_actor is not None else ""
    return (own == actor_public_id or ProxyGrant.objects.filter(
        person=person, delegate__public_id=actor_public_id, **_scope()).exists())


def record_check(
    *, person_public_id: str, evidence_type: str, outcome: str, organization_ref: str,
    actor_public_id: str, valid_days: int | None = None, clock: Clock | None = None,
) -> dict[str, Any]:
    """Record that a person at this clinic checked this patient's identity. The caller has checked
    the person may act for patients of this clinic."""
    if evidence_type not in EvidenceType.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["evidence_type"])
    if outcome not in ProofingOutcome.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["outcome"])
    if valid_days is not None and (
            isinstance(valid_days, bool) or not isinstance(valid_days, int)
            or not 1 <= valid_days <= MAX_VALID_DAYS):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["valid_days"])
    now = utc_now(clock)
    with transaction.atomic():
        person = _own(Person, person_public_id)
        if _is_party(person, actor_public_id):
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        plain = _plain(person)
        if not plain["name"] or not plain["birth_date"]:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["details"])   # nothing to check
        if open_case_people([person.public_id]):
            raise ZoikoError(ErrorCode.HOLD_ACTIVE)       # a possible duplicate must be settled
        record = ProofingRecord.objects.create(
            person=person, evidence_type=evidence_type, outcome=outcome,
            organization_ref=organization_ref, checked_by_ref=actor_public_id, checked_at=now,
            valid_until=(now + dt.timedelta(days=valid_days or DEFAULT_VALID_DAYS)
                         if outcome == ProofingOutcome.PASSED else None),
            details_digest=_details_digest(person), **_scope())
        status = refresh_status(person, clock=clock, announce=False)
        _audit(actor_public_id, "identity.check", "person", person.public_id, outcome=outcome)
        emit(event_type="identity.subject.proofed.v1", aggregate_type="person",
             aggregate_ref=person.public_id,
             payload={"person_ref": person.public_id, "outcome": outcome, "status": status},
             dedupe_key=f"proof-record:{record.public_id}")
        return status_of(person.public_id, clock=clock)


def withdraw(
    *, person_public_id: str, reason: str, actor_public_id: str, clock: Clock | None = None
) -> dict[str, Any]:
    """Take back every check that makes this patient verified (a mistake, or a fraud concern)."""
    if reason not in WITHDRAW_REASONS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["reason"])
    now = utc_now(clock)
    with transaction.atomic():
        person = _own(Person, person_public_id)
        if _is_party(person, actor_public_id):
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        ProofingRecord.objects.filter(
            person=person, outcome=ProofingOutcome.PASSED, withdrawn_at__isnull=True,
            **_scope()).update(withdrawn_at=now, withdrawn_by_ref=actor_public_id,
                               withdraw_reason=reason)
        refresh_status(person, clock=clock)
        _audit(actor_public_id, "identity.withdraw", "person", person.public_id, reason=reason)
        return status_of(person.public_id, clock=clock)


def status_of(person_public_id: str, *, clock: Clock | None = None) -> dict[str, Any]:
    """Whether this patient is verified right now, by which check, and why not if not."""
    now = utc_now(clock)
    person = _own(Person, person_public_id)
    refresh_status(person, clock=clock)
    valid = _latest_valid(person, now)
    latest = ProofingRecord.objects.filter(person=person, **_scope()).order_by(
        "-checked_at").first()
    why = ""
    if valid is None and latest is not None:
        if latest.withdrawn_at is not None:
            why = "withdrawn"
        elif latest.outcome == ProofingOutcome.FAILED:
            why = "check_failed"
        elif latest.valid_until is not None and latest.valid_until <= now:
            why = "expired"
        else:
            why = "details_changed"
    shown = valid or latest
    return {
        "person": person.public_id, "status": person.identity_status,
        "checked_on": valid.checked_at.date().isoformat() if valid else None,
        "valid_until": (valid.valid_until.date().isoformat()
                        if valid and valid.valid_until else None),
        "clinic_ref": valid.organization_ref if valid else "",
        "why_not": why, "last_check": shown.checked_at.isoformat() if shown else None,
        "under_review": bool(open_case_people([person.public_id])),
    }


def history(person_public_id: str) -> list[dict[str, Any]]:
    """Every check ever made on this patient, newest first (staff only: the caller authorizes)."""
    person = _own(Person, person_public_id)
    return [
        {"id": r.public_id, "evidence_type": r.evidence_type, "outcome": r.outcome,
         "organization_ref": r.organization_ref, "checked_by_ref": r.checked_by_ref,
         "checked_at": r.checked_at.isoformat(),
         "valid_until": r.valid_until.isoformat() if r.valid_until else None,
         "withdrawn": r.withdrawn_at is not None, "withdraw_reason": r.withdraw_reason}
        for r in ProofingRecord.objects.filter(person=person, **_scope()).order_by("-checked_at")
    ]


def refresh_expired(*, limit: int = 500, clock: Clock | None = None) -> int:
    """For the worker: patients whose stored status says verified but whose check has since
    expired. Returns how many were corrected."""
    fixed = 0
    for person in Person.objects.filter(identity_status=IdentityStatus.VERIFIED,
                                        **_scope())[:limit]:
        if refresh_status(person, clock=clock) != IdentityStatus.VERIFIED:
            fixed += 1
    return fixed
