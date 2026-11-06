"""Suspending or reinstating a customer takes two different people.

One support person REQUESTS it, with a reason. Nothing changes until a DIFFERENT support person
APPROVES it; the person who asked can only cancel their own request. A suspended customer cannot
sign in and its open sessions stop working (the sign-in and token checks already require an active
customer). The platform's own tenant can never be suspended.
"""
from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import (
    Status,
    Suspension,
    SuspensionKind,
    SuspensionStatus,
    Tenant,
    TenantType,
)
from .releases import _clean
from .services import context_for, list_tenants

WRITE_PLATFORM = "write:platform"
MAX_LIST = 100
_NEEDS = {SuspensionKind.SUSPEND: Status.ACTIVE, SuspensionKind.REINSTATE: Status.SUSPENDED}
_BECOMES = {SuspensionKind.SUSPEND: Status.SUSPENDED, SuspensionKind.REINSTATE: Status.ACTIVE}
_CUSTOMER_ACTION = {SuspensionKind.SUSPEND: "tenant.suspended",
                    SuspensionKind.REINSTATE: "tenant.reinstated"}


def suspension_dict(row: Suspension) -> dict[str, Any]:
    return {
        "public_id": row.public_id, "customer_ref": row.customer_ref, "kind": row.kind,
        "reason": row.reason, "status": row.status, "requested_by_ref": row.requested_by_ref,
        "decided_by_ref": row.decided_by_ref, "decision_note": row.decision_note,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "created_at": row.created_at.isoformat(),
    }


def _receipt(decision: policy.Decision, actor: str, org: str | None) -> None:
    policy.check_receipt(
        decision, actor_public_id=actor, operation=WRITE_PLATFORM,
        organization_public_id=org, subject_ref=None)


def _customer(customer_public_id: str) -> dict[str, str]:
    found = next((t for t in list_tenants() if t["public_id"] == customer_public_id), None)
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if found["type"] == TenantType.PLATFORM:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["customer"])
    return found


def _own(request_ref: str) -> Suspension:
    ctx = require_context()
    row = Suspension.objects.select_for_update().filter(
        public_id=request_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


def request_change(
    *,
    customer_public_id: str,
    kind: object,
    reason: object,
    actor_public_id: str,
    platform_organization_public_id: str | None,
    decision: policy.Decision,
) -> dict[str, Any]:
    """Ask to suspend an active customer or reinstate a suspended one. Needs the right to write
    the platform. Only one request can be waiting for a customer at a time."""
    _receipt(decision, actor_public_id, platform_organization_public_id)
    if kind not in SuspensionKind.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["action"])
    clean_reason = _clean(reason, "reason", 500, required=True)
    found = _customer(customer_public_id)
    if found["status"] != _NEEDS[SuspensionKind(kind)]:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    try:
        with tenant_transaction(), transaction.atomic():
            row = Suspension.objects.create(
                customer_ref=customer_public_id, kind=kind, reason=clean_reason,
                requested_by_ref=actor_public_id)
            record(actor_ref=actor_public_id, action="platform.suspension_request",
                   object_type="tenant", object_ref=customer_public_id,
                   outcome=AuditOutcome.ALLOWED, details={"kind": kind})
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
    return suspension_dict(row)


def decide_change(
    *,
    request_ref: str,
    approve: bool,
    note: object,
    actor_public_id: str,
    platform_organization_public_id: str | None,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """A DIFFERENT support person approves (which applies it now) or rejects a waiting request.
    The person who asked is refused. Approving checks the customer's status again, so a request
    that no longer fits (already suspended, say) is refused and stays waiting."""
    _receipt(decision, actor_public_id, platform_organization_public_id)
    clean_note = _clean(note, "note", 300, required=False)
    with tenant_transaction(), transaction.atomic():
        row = _own(request_ref)
        if row.status != SuspensionStatus.PENDING:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if row.requested_by_ref == actor_public_id:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        kind = SuspensionKind(row.kind)
        if approve:
            current = _customer(row.customer_ref)
            if current["status"] != _NEEDS[kind]:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            Tenant.objects.filter(public_id=row.customer_ref).update(status=_BECOMES[kind])
        row.status = SuspensionStatus.APPLIED if approve else SuspensionStatus.REJECTED
        row.decided_by_ref, row.decision_note = actor_public_id, clean_note
        row.decided_at = utc_now(clock)
        row.entity_version += 1
        row.save(update_fields=[
            "status", "decided_by_ref", "decision_note", "decided_at", "entity_version",
            "updated_at"])
        record(actor_ref=actor_public_id,
               action="platform.suspension_approve" if approve else "platform.suspension_reject",
               object_type="tenant", object_ref=row.customer_ref, outcome=AuditOutcome.ALLOWED,
               details={"kind": row.kind})
        done = suspension_dict(row)
    if approve:             # the customer's own chain records it too (who did it, no more)
        with tenant_transaction(context_for(done["customer_ref"])):
            record(actor_ref=actor_public_id, action=_CUSTOMER_ACTION[kind], object_type="tenant",
                   object_ref=done["customer_ref"], outcome=AuditOutcome.ALLOWED)
    return done


def cancel_change(
    *,
    request_ref: str,
    actor_public_id: str,
    platform_organization_public_id: str | None,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """The person who asked takes their own waiting request back. Nobody else can."""
    _receipt(decision, actor_public_id, platform_organization_public_id)
    with tenant_transaction(), transaction.atomic():
        row = _own(request_ref)
        if row.status != SuspensionStatus.PENDING:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if row.requested_by_ref != actor_public_id:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        row.status, row.decided_by_ref = SuspensionStatus.CANCELLED, actor_public_id
        row.decided_at = utc_now(clock)
        row.entity_version += 1
        row.save(update_fields=["status", "decided_by_ref", "decided_at", "entity_version",
                                "updated_at"])
        record(actor_ref=actor_public_id, action="platform.suspension_cancel",
               object_type="tenant", object_ref=row.customer_ref, outcome=AuditOutcome.ALLOWED,
               details={"kind": row.kind})
        return suspension_dict(row)


def list_changes(limit: int = 50) -> list[dict[str, Any]]:
    """Waiting requests first, then the newest. The caller has authorized `read:platform`."""
    ctx = require_context()
    rows = list(Suspension.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id))
    rows.sort(key=lambda r: (r.status != SuspensionStatus.PENDING, -r.created_at.timestamp()))
    return [suspension_dict(r) for r in rows[: max(1, min(limit, MAX_LIST))]]
