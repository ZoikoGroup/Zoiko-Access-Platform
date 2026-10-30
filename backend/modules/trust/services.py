"""Creating and changing tenants, organizations, actors and role assignments.

Every function that changes something writes an audit event in the same transaction.
`by` is the public_id of the actor doing it, or "system" for bootstrap and jobs.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, TypeVar

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet

from foundation.cell_context import (
    CellContext,
    build_context,
    require_context,
    tenant_transaction,
)
from foundation.errors import ErrorCode, ZoikoError
from foundation.models import CellScopedModel
from foundation.timeutil import Clock, TimeError, require_aware, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import (
    Actor,
    ActorType,
    CareRelationship,
    Organization,
    Person,
    ProxyGrant,
    Role,
    RoleAssignment,
    Status,
    Tenant,
    TenantType,
)
from .roles_seed import DEFAULT_ROLES

M = TypeVar("M", bound=CellScopedModel)


def _audit(by: str, action: str, object_type: str, object_ref: str, **details: str | int) -> None:
    record(actor_ref=by, action=action, object_type=object_type, object_ref=object_ref,
           outcome=AuditOutcome.ALLOWED, details=dict(details))


def _own(model: type[M], public_id: str) -> M:
    """Fetch by public id inside the bound tenant. Filtered here AND by row-level security."""
    ctx = require_context()
    found = model._default_manager.filter(
        public_id=public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id
    ).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def create_tenant(*, name: str, type: TenantType, authority_ref: str = "") -> Tenant:
    """Bootstrap a tenant, give it the default roles, and open its audit chain."""
    if not name.strip():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["name"])
    try:
        with transaction.atomic():
            tenant = Tenant.objects.create(
                cell_id=settings.ZOIKO_CELL_ID, name=name.strip(), type=type,
                authority_ref=authority_ref,
            )
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
    with tenant_transaction(build_context(tenant.id)):
        for code, role_name, workspace, prohibitions in DEFAULT_ROLES:
            Role.objects.create(
                role_code=code, name=role_name, workspace=workspace,
                default_prohibitions=prohibitions,
            )
        _audit("system", "tenant.create", "tenant", tenant.public_id, roles=len(DEFAULT_ROLES))
    return tenant


def create_organization(*, name: str, by: str = "system") -> Organization:
    if not name.strip():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["name"])
    with transaction.atomic():
        try:
            with transaction.atomic():
                org = Organization.objects.create(name=name.strip())
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
        _audit(by, "organization.create", "organization", org.public_id)
        return org


def register_actor(
    *, actor_type: ActorType, issuer: str, subject: str, by: str = "system"
) -> Actor:
    """Create the actor for an identity-provider subject. Repeating it returns the same actor."""
    if not issuer.strip() or not subject.strip():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["issuer", "subject"])
    ctx = require_context()
    existing = Actor.objects.filter(
        cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, issuer=issuer, subject=subject
    ).first()
    if existing is not None:
        return existing
    with transaction.atomic():
        actor = Actor.objects.create(actor_type=actor_type, issuer=issuer, subject=subject)
        _audit(by, "actor.register", "actor", actor.public_id, actor_type=actor_type)
        return actor


def set_actor_status(actor_public_id: str, status: Status, *, by: str) -> Actor:
    with transaction.atomic():
        actor = _own(Actor, actor_public_id)
        actor.status = status
        actor.entity_version += 1
        actor.save(update_fields=["status", "entity_version", "updated_at"])
        _audit(by, "actor.set_status", "actor", actor.public_id, status=status)
        return actor


def _aware(value: dt.datetime, field: str) -> dt.datetime:
    try:
        return require_aware(value)
    except TimeError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field]) from None


def _in_force(queryset: QuerySet[Any], at: dt.datetime) -> QuerySet[Any]:
    return queryset.filter(status=Status.ACTIVE, effective_from__lte=at).filter(
        Q(effective_until__isnull=True) | Q(effective_until__gt=at)
    )


def assign_role(
    *,
    actor_public_id: str,
    organization_public_id: str,
    role_code: str,
    effective_until: dt.datetime | None = None,
    sponsor_public_id: str | None = None,
    by: str,
    clock: Clock | None = None,
) -> RoleAssignment:
    """Let an actor hold a role at an organization. This grants no access by itself."""
    now = utc_now(clock)
    if effective_until is not None:
        if _aware(effective_until, "effective_until") <= now:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["effective_until"])
    with transaction.atomic():
        actor = _own(Actor, actor_public_id)
        org = _own(Organization, organization_public_id)
        ctx = require_context()
        role = Role.objects.filter(
            cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, role_code=role_code
        ).first()
        if role is None:
            # A tenant created before a catalog role existed gets it on first use.
            known = next((r for r in DEFAULT_ROLES if r[0] == role_code), None)
            if known is None:
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["role_code"])
            role = Role.objects.create(
                role_code=known[0], name=known[1], workspace=known[2],
                default_prohibitions=known[3])
        if actor.status != Status.ACTIVE or org.status != Status.ACTIVE:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        sponsor = _own(Actor, sponsor_public_id) if sponsor_public_id else None
        if _in_force(RoleAssignment.objects.filter(actor=actor, organization=org, role=role), now):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        assignment = RoleAssignment.objects.create(
            actor=actor, organization=org, role=role, sponsor=sponsor,
            effective_from=now, effective_until=effective_until,
        )
        _audit(by, "role.assign", "role_assignment", assignment.public_id,
               actor=actor.public_id, role=role_code)
        return assignment


def end_assignment(
    assignment_public_id: str, *, reason: str, by: str, clock: Clock | None = None
) -> RoleAssignment:
    """Remove a role from now on. The history stays; nothing is deleted."""
    with transaction.atomic():
        assignment = _own(RoleAssignment, assignment_public_id)
        if assignment.status == Status.ENDED:
            return assignment
        assignment.status = Status.ENDED
        assignment.effective_until = utc_now(clock)
        assignment.removal_reason = reason
        assignment.entity_version += 1
        assignment.save(update_fields=[
            "status", "effective_until", "removal_reason", "entity_version", "updated_at"])
        _audit(by, "role.end", "role_assignment", assignment.public_id, reason=reason)
        return assignment


def active_roles(
    actor_public_id: str, *, at: dt.datetime | None = None
) -> list[tuple[str, str]]:
    """(role_code, organization public_id) pairs in force at a moment.

    Empty if the actor or the organization is not active. Holding a role is only one input
    to an access decision; the policy module weighs the rest.
    """
    when = utc_now() if at is None else _aware(at, "at")
    actor = _own(Actor, actor_public_id)
    if actor.status != Status.ACTIVE:
        return []
    rows = _in_force(
        RoleAssignment.objects.filter(actor=actor, organization__status=Status.ACTIVE), when
    ).select_related("role", "organization")
    return sorted((r.role.role_code, r.organization.public_id) for r in rows)


def list_tenants() -> list[dict[str, str]]:
    """Every tenant in this cell (name and ids only). Not tenant-scoped, by design."""
    rows = Tenant.objects.filter(cell_id=settings.ZOIKO_CELL_ID).order_by("created_at")
    return [
        {"public_id": t.public_id, "name": t.name, "type": t.type, "status": t.status,
         "self_registration": str(t.self_registration).lower(),
         "created_at": t.created_at.isoformat()}
        for t in rows
    ]


def overview() -> dict[str, Any]:
    """Everything in the bound tenant that a screen needs: orgs, actors with roles, catalog."""
    ctx = require_context()
    scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
    assignments = RoleAssignment.objects.filter(**scope).select_related("role", "organization")
    by_actor: dict[Any, list[dict[str, str]]] = {}
    for a in assignments.order_by("created_at"):
        by_actor.setdefault(a.actor_id, []).append({
            "assignment": a.public_id, "role": a.role.role_code,
            "organization": a.organization.public_id, "status": a.status,
            "until": a.effective_until.isoformat() if a.effective_until else "",
        })
    return {
        "organizations": [
            {"public_id": o.public_id, "name": o.name, "status": o.status}
            for o in Organization.objects.filter(**scope).order_by("created_at")
        ],
        "actors": [
            {"public_id": a.public_id, "type": a.actor_type, "issuer": a.issuer,
             "subject": a.subject, "status": a.status, "roles": by_actor.get(a.pk, [])}
            for a in Actor.objects.filter(**scope).order_by("created_at")
        ],
        "roles": [
            {"code": r.role_code, "name": r.name}
            for r in Role.objects.filter(**scope).order_by("role_code")
        ],
        "persons": [
            {"public_id": p.public_id, "status": p.status,
             "linked_actor": p.linked_actor.public_id if p.linked_actor else ""}
            for p in Person.objects.filter(**scope).select_related("linked_actor")
            .order_by("created_at")
        ],
        "care": [
            {"public_id": c.public_id, "actor": c.actor.public_id, "person": c.person.public_id,
             "basis": c.basis, "status": c.status, "contested": c.contested}
            for c in CareRelationship.objects.filter(**scope).select_related("actor", "person")
            .order_by("created_at")
        ],
        "proxies": [
            {"public_id": g.public_id, "delegate": g.delegate.public_id,
             "person": g.person.public_id, "type": g.relationship_type, "status": g.status}
            for g in ProxyGrant.objects.filter(**scope).select_related("delegate", "person")
            .order_by("created_at")
        ],
    }


def context_for(tenant_public_id: str) -> CellContext:
    """The cell and tenant context for a tenant's public id. For trusted code only (dev
    console, jobs): a real request gets its context from the login token."""
    tenant = Tenant.objects.filter(
        public_id=tenant_public_id, cell_id=settings.ZOIKO_CELL_ID
    ).first()
    if tenant is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return build_context(tenant.id)


def get_organization(public_id: str) -> dict[str, str] | None:
    """An organization of the bound tenant as a plain dict, or None. For other modules."""
    ctx = require_context()
    org = Organization.objects.filter(
        public_id=public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id
    ).first()
    if org is None:
        return None
    return {"public_id": org.public_id, "name": org.name, "status": org.status}


def organization_names() -> dict[str, str]:
    """public_id -> name for every organization of the bound tenant."""
    ctx = require_context()
    rows = Organization.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
    return {o.public_id: o.name for o in rows}


def set_self_registration(enabled: bool, *, by: str) -> None:
    """Let patients create their own account in this tenant (or stop them)."""
    ctx = require_context()
    with transaction.atomic():
        Tenant.objects.filter(id=ctx.tenant_id, cell_id=ctx.cell_id).update(
            self_registration=enabled)
        _audit(by, "tenant.self_registration", "tenant", str(ctx.tenant_id)[:36],
               enabled=int(enabled))


def is_clinician_at(actor_public_id: str, organization_public_id: str) -> bool:
    """Is this an ACTIVE login holding an active doctor role (R03) at this organization?"""
    return any(
        role == "R03" and org == organization_public_id
        for role, org in _roles_if_active(actor_public_id)
    )


def _roles_if_active(actor_public_id: str) -> list[tuple[str, str]]:
    try:
        return active_roles(actor_public_id)
    except ZoikoError:
        return []
