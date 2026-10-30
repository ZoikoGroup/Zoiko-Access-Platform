"""The access decision point: may THIS actor do THIS operation, for THIS purpose, right now?

Rules of the engine
- Default is DENY. Any missing, unknown or unverifiable input ends in a denial.
- A role is only one input. Purpose, organization and (later) a verified relationship to the
  patient must also hold.
- Hard prohibitions are checked before anything else and cannot be unlocked by other roles' rules.
- Every decision, allowed or denied, is stored and audited in the same transaction.
- The caller's claims are never trusted: roles come from the trust module, never from the request.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Protocol

from django.db import connection, models, transaction

from foundation.cell_context import require_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.request_context import get_request_id
from foundation.timeutil import utc_now
from modules.evidence.api import AuditOutcome
from modules.evidence.api import record as audit
from modules.trust import api as trust

from .models import AccessDecision
from .rules import (
    HARD_PROHIBITIONS,
    POLICY_VERSION,
    PURPOSES,
    RESOURCE_CLASSES,
    ROLE_PURPOSES,
    RULES,
    VERBS,
)

_OPERATION = re.compile(r"^([a-z]+):([a-z_]+)$")
_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class Reason(models.TextChoices):
    ALLOWED = "ALLOWED"
    ACTOR_UNKNOWN = "ACTOR_UNKNOWN"
    NO_ROLE = "NO_ROLE"
    UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
    HARD_PROHIBITION = "HARD_PROHIBITION"
    ROLE_NOT_PERMITTED = "ROLE_NOT_PERMITTED"
    PURPOSE_NOT_ALLOWED = "PURPOSE_NOT_ALLOWED"
    ORG_REQUIRED = "ORG_REQUIRED"
    ORG_MISMATCH = "ORG_MISMATCH"
    RELATIONSHIP_UNVERIFIED = "RELATIONSHIP_UNVERIFIED"


# When several roles each fail for a different reason, report the one closest to success.
_CLOSEST_FIRST = (
    Reason.RELATIONSHIP_UNVERIFIED, Reason.ORG_MISMATCH, Reason.ORG_REQUIRED,
    Reason.PURPOSE_NOT_ALLOWED, Reason.ROLE_NOT_PERMITTED, Reason.HARD_PROHIBITION,
)


class RelationshipChecker(Protocol):
    """Answers "is this relationship to the patient verified?". The real one reads the
    care-relationship and delegation tables, which do not exist yet."""

    def verified(self, *, actor_public_id: str, condition: str, subject_ref: str) -> bool: ...


class TrustRelationships:
    """The real checker: asks the trust module whether the link exists right now."""

    def verified(self, *, actor_public_id: str, condition: str, subject_ref: str) -> bool:
        return trust.relationship_verified(
            actor_public_id=actor_public_id, condition=condition, subject_ref=subject_ref
        )


class NoRelationships:
    """Verifies nothing, so every patient-linked door stays shut. Useful in tests."""

    def verified(self, *, actor_public_id: str, condition: str, subject_ref: str) -> bool:
        return False


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: Reason
    decision_public_id: str
    role_code: str = ""

    def require(self) -> None:
        """Raise unless allowed. Use at the start of any protected operation."""
        if self.allowed:
            return
        if self.reason == Reason.RELATIONSHIP_UNVERIFIED:
            raise ZoikoError(ErrorCode.SUBJECT_LINK_UNVERIFIED)
        raise ZoikoError(ErrorCode.POLICY_DENIED)


def _judge_role(
    role: str, org_held: str, verb: str, cls: str, purpose: str, org_asked: str | None,
    subject: str | None, actor: str, checker: RelationshipChecker,
) -> Reason:
    if cls in HARD_PROHIBITIONS.get(role, frozenset()):
        return Reason.HARD_PROHIBITION
    verbs, condition = RULES.get(role, {}).get(cls, (frozenset(), ""))
    if verb not in verbs:
        return Reason.ROLE_NOT_PERMITTED
    if purpose not in ROLE_PURPOSES.get(role, frozenset()):
        return Reason.PURPOSE_NOT_ALLOWED
    if condition == "ORG":
        if org_asked is None:
            return Reason.ORG_REQUIRED
        if org_asked != org_held:
            return Reason.ORG_MISMATCH
    elif condition in ("OWN", "REL", "PROXY") and not (
        subject is not None
        and checker.verified(actor_public_id=actor, condition=condition, subject_ref=subject)
    ):
        return Reason.RELATIONSHIP_UNVERIFIED
    return Reason.ALLOWED


def _evaluate(
    actor: str, operation: str, purpose: str, org_asked: str | None, subject: str | None,
    checker: RelationshipChecker,
) -> tuple[Reason, str]:
    match = _OPERATION.match(operation)
    if not match or match[1] not in VERBS or match[2] not in RESOURCE_CLASSES:
        return Reason.UNKNOWN_OPERATION, ""
    if purpose not in PURPOSES:
        return Reason.PURPOSE_NOT_ALLOWED, ""
    verb, cls = match[1], match[2]
    try:
        held = trust.active_roles(actor)
    except ZoikoError:
        return Reason.ACTOR_UNKNOWN, ""
    if not held:
        return Reason.NO_ROLE, ""
    failures: set[Reason] = set()
    for role, org_held in held:
        reason = _judge_role(
            role, org_held, verb, cls, purpose, org_asked, subject, actor, checker
        )
        if reason == Reason.ALLOWED:
            return Reason.ALLOWED, role
        failures.add(reason)
    for reason in _CLOSEST_FIRST:
        if reason in failures:
            return reason, ""
    return Reason.ROLE_NOT_PERMITTED, ""


def decide(
    *,
    actor_public_id: str,
    operation: str,
    purpose_code: str,
    organization_public_id: str | None = None,
    subject_ref: str | None = None,
    relationships: RelationshipChecker | None = None,
) -> Decision:
    """Decide, store the decision and audit it. Needs a bound tenant (see cell_context)."""
    actor_ok = bool(_REF.match(actor_public_id))
    actor = actor_public_id if actor_ok else "unknown"
    org = organization_public_id if organization_public_id and _REF.match(
        organization_public_id) else None
    subject = subject_ref if subject_ref and _REF.match(subject_ref) else None
    if not actor_ok:
        reason, role = Reason.ACTOR_UNKNOWN, ""
    else:
        reason, role = _evaluate(
            actor, operation, purpose_code, org, subject, relationships or TrustRelationships()
        )
    allowed = reason == Reason.ALLOWED
    with transaction.atomic():
        row = AccessDecision.objects.create(
            actor_ref=actor,
            operation=operation[:64],
            purpose_code=purpose_code[:32],
            organization_ref=org or "",
            subject_ref=subject or "",
            allowed=allowed,
            reason_code=reason.value,
            role_code=role,
            policy_version=POLICY_VERSION,
            request_id=get_request_id(),
        )
        audit(
            actor_ref=actor,
            action="access.decide",
            object_type="access_decision",
            object_ref=row.public_id,
            outcome=AuditOutcome.ALLOWED if allowed else AuditOutcome.DENIED,
            reason_code=reason.value,
        )
    return Decision(allowed, reason, row.public_id, role)


def recent_decisions(limit: int = 50) -> list[dict[str, object]]:
    """Newest access decisions of the current tenant, as plain dicts."""
    ctx = require_context()
    rows = AccessDecision.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).order_by(
        "-created_at")[: max(1, min(limit, 200))]
    return [
        {
            "at": r.created_at.isoformat(), "actor": r.actor_ref, "operation": r.operation,
            "purpose": r.purpose_code, "organization": r.organization_ref,
            "subject": r.subject_ref, "allowed": r.allowed, "reason": r.reason_code,
            "role": r.role_code,
        }
        for r in rows
    ]


RECEIPT_MAX_AGE = dt.timedelta(minutes=2)


def authorize(
    *,
    actor_public_id: str,
    operation: str,
    purpose_code: str,
    organization_public_id: str | None = None,
    subject_ref: str | None = None,
    relationships: RelationshipChecker | None = None,
) -> Decision:
    """Decide in its OWN committed transaction, then raise if denied.

    Why separate: raising inside the caller's transaction would roll back the stored denial
    and its audit event. A denial must always leave a trace, so this must run outside a
    transaction, with a tenant bound (see cell_context.bind_context).
    """
    if connection.in_atomic_block:
        raise RuntimeError("authorize() must run outside a transaction so a denial is recorded")
    ctx = require_context()
    with tenant_transaction(ctx):
        decision = decide(
            actor_public_id=actor_public_id, operation=operation, purpose_code=purpose_code,
            organization_public_id=organization_public_id, subject_ref=subject_ref,
            relationships=relationships,
        )
    decision.require()
    return decision


def check_receipt(
    decision: Decision,
    *,
    actor_public_id: str,
    operation: str,
    organization_public_id: str | None = None,
    subject_ref: str | None = None,
) -> None:
    """A domain function proves it was authorized: the stored decision must be an ALLOW for
    exactly this actor, operation, organization and patient, and recent. Else POLICY_DENIED."""
    ctx = require_context()
    row = AccessDecision.objects.filter(
        public_id=decision.decision_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id
    ).first()
    fresh = row is not None and utc_now() - row.created_at <= RECEIPT_MAX_AGE
    if (
        row is None
        or not fresh
        or not row.allowed
        or row.actor_ref != actor_public_id
        or row.operation != operation
        or row.organization_ref != (organization_public_id or "")
        or row.subject_ref != (subject_ref or "")
    ):
        raise ZoikoError(ErrorCode.POLICY_DENIED)


def decision_counts(since: dt.datetime) -> dict[str, int]:
    """How many access checks this tenant made since `since`, and how many were denied."""
    ctx = require_context()
    rows = AccessDecision.objects.filter(
        cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, created_at__gte=since)
    return {"total": rows.count(), "denied": rows.filter(allowed=False).count()}
