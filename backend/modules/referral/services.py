"""Issuing a referral and deciding on it.

Access works as in scheduling: both writes need a policy RECEIPT for exactly this actor,
operation, organization and patient. On top of the receipt, ONLY a doctor (R03) may issue a
referral, and only the receiving organization's operations staff (R04, R13) may decide.
Every change is audited and emits an outbox event in the same transaction.
"""
from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.catalog import api as catalog
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import OPEN_STATES, Referral, ReferralState, Urgency

WRITE_REFERRAL = "write:referral"
ISSUING_ROLES = frozenset({"R03"})
DECIDING_ROLES = frozenset({"R04", "R13"})
DECISIONS = {"accepted": ReferralState.ACCEPTED, "needs_info": ReferralState.NEEDS_INFO,
             "declined": ReferralState.DECLINED}
REASON_REQUIRED = frozenset({"needs_info", "declined"})
MAX_TEXT = 500


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _own(referral_ref: str) -> Referral:
    found = Referral.objects.filter(public_id=referral_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def referral_dict(r: Referral) -> dict[str, Any]:
    return {
        "public_id": r.public_id, "person_ref": r.person_ref, "referrer_ref": r.referrer_ref,
        "from_organization_ref": r.from_organization_ref, "service_ref": r.service_ref,
        "destination_organization_ref": r.destination_organization_ref, "reason": r.reason,
        "urgency": r.urgency, "state": r.state, "decided_by_ref": r.decided_by_ref,
        "decision_reason": r.decision_reason,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "created_at": r.created_at.isoformat(),
    }


def _event(event_type: str, r: Referral) -> None:
    # Which referral changed, nothing about the patient or the reason.
    emit(event_type=event_type, aggregate_type="referral", aggregate_ref=r.public_id,
         payload={"referral_ref": r.public_id, "state": r.state})


def _clean(value: str, field: str) -> str:
    value = (value or "").strip()
    if not 1 <= len(value) <= MAX_TEXT:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return value


def issue(
    *, person_ref: str, service_ref: str, reason: str, urgency: str, actor_public_id: str,
    from_organization_ref: str, decision: policy.Decision,
) -> dict[str, Any]:
    """A doctor sends a patient to a service (of any organization of this customer)."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_REFERRAL,
        organization_public_id=from_organization_ref, subject_ref=person_ref)
    if decision.role_code not in ISSUING_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    reason = _clean(reason, "reason")
    if urgency not in Urgency.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["urgency"])
    service = catalog.get_service(service_ref)
    if service["status"] != "active":
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["service"])
    try:
        with transaction.atomic():
            referral = Referral.objects.create(
                person_ref=person_ref, referrer_ref=actor_public_id,
                from_organization_ref=from_organization_ref, service_ref=service_ref,
                destination_organization_ref=service["organization_ref"], reason=reason,
                urgency=urgency, **_scope())
            record(actor_ref=actor_public_id, action="referral.issue", object_type="referral",
                   object_ref=referral.public_id, outcome=AuditOutcome.ALLOWED)
            _event("referral.issued.v1", referral)
            return referral_dict(referral)
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None   # already referred there


def decide(
    *, referral_ref: str, outcome: str, reason: str, actor_public_id: str,
    decision: policy.Decision, clock: Clock | None = None,
) -> dict[str, Any]:
    """The receiving organization accepts, asks for more information, or declines."""
    with transaction.atomic():
        referral = _own(referral_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_REFERRAL,
            organization_public_id=referral.destination_organization_ref,
            subject_ref=referral.person_ref)
        if decision.role_code not in DECIDING_ROLES:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if outcome not in DECISIONS:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["decision"])
        reason = reason.strip() if outcome not in REASON_REQUIRED else _clean(reason, "reason")
        if len(reason) > MAX_TEXT:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["reason"])
        if referral.state not in OPEN_STATES:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)         # already accepted or declined
        referral.state = DECISIONS[outcome]
        referral.decided_by_ref = actor_public_id
        referral.decision_reason = reason
        referral.decided_at = utc_now(clock)
        referral.entity_version += 1
        referral.save(update_fields=["state", "decided_by_ref", "decision_reason", "decided_at",
                                     "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action=f"referral.{outcome}", object_type="referral",
               object_ref=referral.public_id, outcome=AuditOutcome.ALLOWED)
        _event("referral.decided.v1", referral)
        return referral_dict(referral)


def get_referral(referral_ref: str) -> dict[str, Any]:
    return referral_dict(_own(referral_ref))


def list_referrals(
    *, referrer_ref: str = "", person_refs: list[str] | None = None,
    destination_organization_ref: str = "", limit: int = 200,
) -> list[dict[str, Any]]:
    """Newest first. Exactly the filters given are applied; the caller decides which are allowed."""
    rows = Referral.objects.filter(**_scope())
    if referrer_ref:
        rows = rows.filter(referrer_ref=referrer_ref)
    if person_refs is not None:
        rows = rows.filter(person_ref__in=person_refs)
    if destination_organization_ref:
        rows = rows.filter(destination_organization_ref=destination_organization_ref)
    return [referral_dict(r) for r in rows.order_by("-created_at")[:limit]]


def referral_counts() -> dict[str, int]:
    rows = Referral.objects.filter(**_scope())
    return {"total": rows.count(), "open": rows.filter(state__in=OPEN_STATES).count()}
