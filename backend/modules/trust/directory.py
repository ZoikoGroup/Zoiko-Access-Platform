"""Read-only views of a tenant for the staff screens.

Privacy rule applied here: staff screens never show a patient's or guardian's login (email).
Staff appear by login; patients and guardians appear as "Patient ab12cd" / "Guardian ab12cd".
"""
from __future__ import annotations

from typing import Any

from foundation.cell_context import require_context

from .models import (
    Actor,
    CareRelationship,
    Credential,
    Organization,
    Person,
    ProxyGrant,
    RoleAssignment,
    Status,
)

_PRIVATE_TYPES = ("patient", "proxy")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def label_for(actor_type: str, subject: str, public_id: str) -> str:
    """How an actor may be shown to staff."""
    if actor_type == "patient":
        return f"Patient {public_id[:6]}"
    if actor_type == "proxy":
        return f"Guardian {public_id[:6]}"
    return subject


def actor_labels() -> dict[str, str]:
    """public_id -> label for every actor of the bound tenant."""
    rows = Actor.objects.filter(**_scope()).values_list("public_id", "actor_type", "subject")
    return {pid: label_for(kind, subject, pid) for pid, kind, subject in rows}


def staff_directory() -> dict[str, Any]:
    """Organizations and STAFF logins with the roles they hold."""
    scope = _scope()
    orgs = list(Organization.objects.filter(**scope).order_by("created_at"))
    names = {o.public_id: o.name for o in orgs}
    held: dict[Any, list[dict[str, str]]] = {}
    assignments = RoleAssignment.objects.filter(**scope).select_related("role", "organization")
    for a in assignments.order_by("created_at"):
        held.setdefault(a.actor_id, []).append({
            "role": a.role.role_code, "organization_id": a.organization.public_id,
            "organization_name": names.get(a.organization.public_id, ""), "status": a.status,
        })
    staff = Actor.objects.filter(**scope).exclude(actor_type__in=_PRIVATE_TYPES)
    pending = set(
        Credential.objects.filter(email__gt="", email_verified_at__isnull=True, **scope)
        .values_list("actor_id", flat=True)
    )
    return {
        "organizations": [{"id": o.public_id, "name": o.name, "status": o.status} for o in orgs],
        "staff": [
            {"id": a.public_id, "login": a.subject, "type": a.actor_type, "status": a.status,
             "roles": held.get(a.pk, []), "pending_invite": a.pk in pending}
            for a in staff.order_by("created_at")
        ],
    }


def patient_registry() -> dict[str, Any]:
    """Patients with who treats them and who may act for them. No emails."""
    scope = _scope()
    labels = actor_labels()
    care: dict[Any, list[dict[str, Any]]] = {}
    links = CareRelationship.objects.filter(**scope).select_related("actor")
    for c in links.order_by("created_at"):
        care.setdefault(c.person_id, []).append({
            "id": c.public_id, "clinician": labels.get(c.actor.public_id, ""), "basis": c.basis,
            "status": c.status, "contested": c.contested,
        })
    proxies: dict[Any, list[dict[str, str]]] = {}
    for g in ProxyGrant.objects.filter(**scope).select_related("delegate").order_by("created_at"):
        proxies.setdefault(g.person_id, []).append({
            "id": g.public_id, "guardian": labels.get(g.delegate.public_id, ""),
            "type": g.relationship_type, "status": g.status,
        })
    return {"patients": [
        {"id": p.public_id, "status": p.status, "identity_status": p.identity_status,
         "has_login": p.linked_actor_id is not None, "care": care.get(p.pk, []),
         "proxies": proxies.get(p.pk, [])}
        for p in Person.objects.filter(**scope).order_by("created_at")
    ]}


def counts() -> dict[str, int]:
    scope = _scope()
    return {
        "patients": Person.objects.filter(**scope).count(),
        "organizations": Organization.objects.filter(**scope).count(),
        "staff": Actor.objects.filter(**scope).exclude(actor_type__in=_PRIVATE_TYPES).count(),
        "roles_in_force": RoleAssignment.objects.filter(status=Status.ACTIVE, **scope).count(),
        "care_links": CareRelationship.objects.filter(
            status=Status.ACTIVE, contested=False, **scope).count(),
    }
