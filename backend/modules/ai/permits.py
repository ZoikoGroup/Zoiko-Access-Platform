"""AI use permits: who may switch an AI feature on, and the rules that keep it off otherwise.

Default deny: a feature with no permit in the AUTHORIZED state (or SHADOW, for a dry run) cannot
run. A permit pins the exact model, prompt and tool list by fingerprint. Tools are
whitelist-only; the two tools the docs forbid to every agent can never be on a list. Authorizing
needs two different people who are not the creator, and an expiry. Features the docs treat as
regulated devices (level A3, lanes C and D) cannot be authorized here at all: they need a
regulatory qualification this system does not have.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import AiPermitEvent, AiUsePermit, Lane, Level, PermitState

# Agents never order medication or release critical results (docs, file 05 section G rules).
FORBIDDEN_TOOLS = frozenset({"order_medication", "release_critical_result"})
NEEDS_REGULATORY_LEVELS = frozenset({Level.A3})
NEEDS_REGULATORY_LANES = frozenset({Lane.DECISION_INFORMING, Lane.DEVICE_CONTROLLED})
MIN_APPROVERS = 2
MAX_VALIDITY_DAYS = 365

S = PermitState
TRANSITIONS: dict[str, frozenset[str]] = {
    S.PROPOSED: frozenset({S.QUALIFICATION, S.RETIRED}),
    S.QUALIFICATION: frozenset({S.DESIGN_CONTROL, S.RETIRED}),
    S.DESIGN_CONTROL: frozenset({S.VALIDATION, S.RETIRED}),
    S.VALIDATION: frozenset({S.SANDBOX, S.RETIRED}),
    S.SANDBOX: frozenset({S.SHADOW, S.RETIRED}),
    S.SHADOW: frozenset({S.RELEASE_REVIEW, S.RETIRED}),
    S.RELEASE_REVIEW: frozenset({S.AUTHORIZED, S.RETIRED}),
    S.AUTHORIZED: frozenset({S.SUSPENDED, S.CHANGE_REVIEW, S.RETIRED}),
    S.SUSPENDED: frozenset({S.CORRECTIVE_ACTION, S.RETIRED}),
    S.CORRECTIVE_ACTION: frozenset({S.REVALIDATION, S.RETIRED}),
    S.REVALIDATION: frozenset({S.RELEASE_REVIEW, S.RETIRED}),
    # GHE-23 (state model): CHANGE_REVIEW leads to VALIDATION when the change is material, or to
    # AUTHORIZED when it is approved as not material. (The shorter schema notes omit this edge.)
    S.CHANGE_REVIEW: frozenset({S.VALIDATION, S.AUTHORIZED, S.RETIRED}),
    S.RETIRED: frozenset(),
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_FEATURE = re.compile(r"^[a-z][a-z0-9_.]{2,59}$")
_TOOL = re.compile(r"^[a-z][a-z0-9_.]{1,59}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(*fields: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=list(fields))


def _tools(value: list[str] | None, field: str) -> list[str]:
    tools = list(value or [])
    if len(set(tools)) != len(tools) or any(not _TOOL.match(t) for t in tools) or len(tools) > 20:
        raise _bad(field)
    if FORBIDDEN_TOOLS & set(tools):
        raise _bad(field)
    return tools


def permit_dict(p: AiUsePermit) -> dict[str, Any]:
    return {
        "id": p.public_id,
        "feature_id": p.feature_id,
        "purpose": p.purpose,
        "lane": p.lane,
        "level": p.level,
        "state": p.state,
        "allowed_tools": p.allowed_tools,
        "write_tools": p.write_tools,
        "two_person_tools": p.two_person_tools,
        "max_steps": p.max_steps,
        "approvers": len(p.approver_refs),
        "expiry": p.expiry.isoformat() if p.expiry else None,
    }


def create_permit(
    *,
    feature_id: str,
    purpose: str,
    lane: str,
    level: str,
    model_digest: str,
    prompt_digest: str,
    created_by_ref: str,
    allowed_tools: list[str] | None = None,
    write_tools: list[str] | None = None,
    two_person_tools: list[str] | None = None,
    max_steps: int = 1,
    knowledge_manifest_hash: str = "",
    clinical_owner_ref: str = "",
) -> dict[str, Any]:
    purpose = " ".join((purpose or "").split())
    if not _FEATURE.match(feature_id or ""):
        raise _bad("feature_id")
    if not 5 <= len(purpose) <= 200:
        raise _bad("purpose")
    if lane not in Lane.values:
        raise _bad("lane")
    if level not in Level.values:
        raise _bad("level")
    for value, name in ((model_digest, "model_digest"), (prompt_digest, "prompt_digest")):
        if not _DIGEST.match(value or ""):
            raise _bad(name)
    if knowledge_manifest_hash and not _DIGEST.match(knowledge_manifest_hash):
        raise _bad("knowledge_manifest_hash")
    allowed = _tools(allowed_tools, "allowed_tools")
    writes = _tools(write_tools, "write_tools")
    twos = _tools(two_person_tools, "two_person_tools")
    if not set(writes) <= set(allowed) or not set(twos) <= set(writes):
        raise _bad("write_tools")
    if not 1 <= max_steps <= 10:
        raise _bad("max_steps")
    if not created_by_ref:
        raise _bad("created_by")
    with transaction.atomic():
        permit = AiUsePermit.objects.create(
            feature_id=feature_id,
            purpose=purpose,
            lane=lane,
            level=level,
            model_digest=model_digest,
            prompt_digest=prompt_digest,
            knowledge_manifest_hash=knowledge_manifest_hash,
            clinical_owner_ref=clinical_owner_ref,
            created_by_ref=created_by_ref,
            allowed_tools=allowed,
            write_tools=writes,
            two_person_tools=twos,
            max_steps=max_steps,
            **_scope(),
        )
        record(
            actor_ref=created_by_ref,
            action="ai.permit.propose",
            object_type="ai_permit",
            object_ref=permit.public_id,
            outcome=AuditOutcome.ALLOWED,
        )
    return permit_dict(permit)


def _own(permit_ref: str) -> AiUsePermit:
    found = AiUsePermit.objects.select_for_update().filter(public_id=permit_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def advance(
    permit_ref: str,
    to_state: str,
    *,
    actor_ref: str,
    reason: str = "",
    approver_refs: list[str] | None = None,
    expiry: dt.datetime | None = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Move a permit one step along the allowed path. Anything off the path is refused."""
    now = utc_now(clock)
    reason = " ".join((reason or "").split())[:300]
    with transaction.atomic():
        p = _own(permit_ref)
        if to_state not in TRANSITIONS.get(p.state, frozenset()):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if to_state == S.AUTHORIZED:
            signers = sorted({*p.approver_refs, *(approver_refs or [])})
            _check_can_authorize(p, signers, expiry, now)
            others = AiUsePermit.objects.filter(
                feature_id=p.feature_id, state=S.AUTHORIZED, **_scope()
            ).exclude(pk=p.pk)
            if others.exists():  # one live permit per feature: retire or suspend the old one first
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            p.approver_refs = signers
            p.expiry = expiry
        if to_state in (S.SUSPENDED, S.RETIRED, S.CHANGE_REVIEW) and not reason:
            raise _bad("reason")
        if to_state == S.CHANGE_REVIEW:
            p.approver_refs = []  # a change needs fresh sign-offs; the old ones do not carry over
        before = p.state
        p.state = to_state
        p.save(update_fields=["state", "approver_refs", "expiry", "updated_at"])
        AiPermitEvent.objects.create(
            permit_ref=p.public_id,
            from_state=before,
            to_state=to_state,
            actor_ref=actor_ref,
            reason=reason,
            **_scope(),
        )
        record(
            actor_ref=actor_ref,
            action=f"ai.permit.{to_state.lower()}",
            object_type="ai_permit",
            object_ref=p.public_id,
            outcome=AuditOutcome.ALLOWED,
        )
    return permit_dict(p)


def _check_can_authorize(
    p: AiUsePermit,
    approvers: list[str] | None,
    expiry: dt.datetime | None,
    now: dt.datetime,
) -> None:
    if p.level in NEEDS_REGULATORY_LEVELS or p.lane in NEEDS_REGULATORY_LANES:
        raise ZoikoError(ErrorCode.CLINICAL_ACCEPTANCE_ABSENT)  # no regulatory qualification exists
    people = {a for a in (approvers or []) if a}
    if len(people) < MIN_APPROVERS or p.created_by_ref in people:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if p.lane == Lane.CLINICAL_CONTENT and not p.clinical_owner_ref:
        raise _bad("clinical_owner")
    if expiry is None or expiry <= now or expiry > now + dt.timedelta(days=MAX_VALIDITY_DAYS):
        raise _bad("expiry")


def check_permit(feature_id: str, *, clock: Clock | None = None) -> AiUsePermit:
    """The gate every AI run passes: an unexpired AUTHORIZED (or SHADOW) permit must exist."""
    now = utc_now(clock)
    live = AiUsePermit.objects.filter(
        feature_id=feature_id, state__in=[S.AUTHORIZED, S.SHADOW], **_scope()
    ).order_by("-created_at")
    usable = [p for p in live if p.state == S.SHADOW or (p.expiry is not None and p.expiry > now)]
    authorized = [p for p in usable if p.state == S.AUTHORIZED]
    chosen = next(iter(authorized or usable), None)
    if chosen is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return chosen


def sign_off(permit_ref: str, *, actor_ref: str) -> dict[str, Any]:
    """Add one person's signature to a permit waiting in RELEASE_REVIEW or CHANGE_REVIEW.

    Never the creator, and each person counts once. Authorizing needs MIN_APPROVERS signatures.
    """
    with transaction.atomic():
        p = _own(permit_ref)
        if p.state not in (S.RELEASE_REVIEW, S.CHANGE_REVIEW):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if not actor_ref or actor_ref == p.created_by_ref or actor_ref in p.approver_refs:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        p.approver_refs = [*p.approver_refs, actor_ref]
        p.save(update_fields=["approver_refs", "updated_at"])
        record(
            actor_ref=actor_ref,
            action="ai.permit.sign",
            object_type="ai_permit",
            object_ref=p.public_id,
            outcome=AuditOutcome.ALLOWED,
        )
    return permit_dict(p)


def list_permits() -> list[dict[str, Any]]:
    """Every permit of this customer, newest first, with its recent steps."""
    permits = list(AiUsePermit.objects.filter(**_scope()).order_by("-created_at")[:200])
    events: dict[str, list[AiPermitEvent]] = {}
    wanted = [p.public_id for p in permits]
    for e in AiPermitEvent.objects.filter(permit_ref__in=wanted, **_scope()).order_by("created_at"):
        events.setdefault(e.permit_ref, []).append(e)
    out = []
    for p in permits:
        row = permit_dict(p)
        row["signers"] = list(p.approver_refs)
        row["created_by"] = p.created_by_ref
        row["steps"] = [
            {
                "from": e.from_state,
                "to": e.to_state,
                "by": e.actor_ref,
                "reason": e.reason,
                "at": e.created_at.isoformat(),
            }
            for e in events.get(p.public_id, [])
        ]
        out.append(row)
    return out
