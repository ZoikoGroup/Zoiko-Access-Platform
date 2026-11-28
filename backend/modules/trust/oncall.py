"""Who is on call for an incident, and what happens when nobody answers (GHE-18 G-01, G-10;
DEV-202).

A serious incident (SEV-0, SEV-1, of unknown severity, or touching patient safety) gets an OWNER and
a DEPUTY from Zoiko's support and safety staff. The owner acknowledges it. If nobody has
acknowledged it within ACK_WITHIN, the worker escalates it to the deputy once: a timeline entry, and
a short e-mail with no incident details. Every step of an incident is written to its timeline, which
is never changed. Closing a SEV-0 or SEV-1 incident needs the restoration notice: what customers and
partners were told (G-10).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from foundation.cell_context import require_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.mail import queue_email
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .email_flows import verified_login_email_of_actor
from .models import (
    Actor,
    Incident,
    IncidentEvent,
    IncidentSeverity,
    IncidentStatus,
    RoleAssignment,
    Status,
)
from .services import context_for, list_tenants

ACK_WITHIN = dt.timedelta(minutes=15)
ESCALATE_EVERY_SECONDS = 60.0
ON_CALL_ROLES = ("R11", "R12")
SERIOUS = (IncidentSeverity.SEV0, IncidentSeverity.SEV1, IncidentSeverity.UNKNOWN)


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def log_event(incident: Incident, kind: str, actor_ref: str, note: str = "") -> None:
    IncidentEvent.objects.create(incident=incident, kind=kind, actor_ref=actor_ref,
                                 note=note[:300], **_scope())


def staff_choices() -> list[str]:
    """Public ids of this (platform) tenant's active logins that hold a support or safety role."""
    rows = RoleAssignment.objects.filter(
        role__role_code__in=ON_CALL_ROLES, status=Status.ACTIVE,
        actor__status=Status.ACTIVE, **_scope()).values_list("actor__public_id", flat=True)
    return sorted(set(rows))


def _own(incident_ref: str) -> Incident:
    row = Incident.objects.select_for_update().filter(public_id=incident_ref, **_scope()).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


def assign(*, incident_ref: str, owner_ref: str, deputy_ref: str, actor_ref: str) -> Incident:
    """Name the owner and the deputy (two different support or safety staff). Re-assigning starts
    the acknowledgement clock again."""
    choices = set(staff_choices())
    if owner_ref not in choices:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["owner"])
    if deputy_ref not in choices or deputy_ref == owner_ref:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["deputy"])
    with transaction.atomic():
        row = _own(incident_ref)
        if row.status != IncidentStatus.OPEN:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.owner_ref, row.deputy_ref = owner_ref, deputy_ref
        row.assigned_at = utc_now()
        row.acknowledged_at, row.acknowledged_by_ref, row.escalated_at = None, "", None
        row.entity_version += 1
        row.save(update_fields=["owner_ref", "deputy_ref", "assigned_at", "acknowledged_at",
                                "acknowledged_by_ref", "escalated_at", "entity_version",
                                "updated_at"])
        log_event(row, "assigned", actor_ref)
        record(actor_ref=actor_ref, action="incident.assign", object_type="incident",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED)
        return row


def acknowledge(*, incident_ref: str, actor_ref: str, clock: Clock | None = None) -> Incident:
    """The owner or the deputy says "I have it". Once."""
    with transaction.atomic():
        row = _own(incident_ref)
        if actor_ref not in (row.owner_ref, row.deputy_ref) or not row.owner_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if row.status != IncidentStatus.OPEN or row.acknowledged_at is not None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.acknowledged_at, row.acknowledged_by_ref = utc_now(clock), actor_ref
        row.entity_version += 1
        row.save(update_fields=["acknowledged_at", "acknowledged_by_ref", "entity_version",
                                "updated_at"])
        log_event(row, "acknowledged", actor_ref)
        record(actor_ref=actor_ref, action="incident.acknowledge", object_type="incident",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED)
        return row


def timeline(incident_ref: str) -> list[dict[str, Any]]:
    row = Incident.objects.filter(public_id=incident_ref, **_scope()).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return [{"kind": e.kind, "actor_ref": e.actor_ref, "note": e.note,
             "at": e.created_at.isoformat()}
            for e in IncidentEvent.objects.filter(incident=row, **_scope()).order_by("created_at")]


def escalate_due(clock: Clock | None = None) -> int:
    """Escalate, once, every serious open incident that nobody acknowledged in time. Bound to the
    platform tenant. Returns how many were escalated."""
    now = utc_now(clock)
    done = 0
    due = Incident.objects.select_for_update(skip_locked=True).filter(
        Q(severity__in=SERIOUS) | Q(safety_impact=True),
        status=IncidentStatus.OPEN, acknowledged_at__isnull=True, escalated_at__isnull=True,
        assigned_at__lte=now - ACK_WITHIN, **_scope()).exclude(owner_ref="")
    with transaction.atomic():
        for row in due:
            row.escalated_at = now
            row.entity_version += 1
            row.save(update_fields=["escalated_at", "entity_version", "updated_at"])
            log_event(row, "escalated", "system", "Not acknowledged in time: sent to the deputy")
            address = verified_login_email_of_actor(row.deputy_ref) if row.deputy_ref else None
            if address:
                queue_email(kind="incident_escalation", to_address=address, values={
                    "app_url": str(getattr(settings, "PLATFORM_APP_URL", ""))})
            # DEV-400: and page the on-call person (PagerDuty). A reference only, never a title.
            from foundation import paging

            paging.trigger(f"zoiko-{settings.ZOIKO_CELL_ID}-incident-{row.public_id}",
                           "A serious incident was not acknowledged in time",
                           severity="critical", runbook="docs/runbooks/README.md")
            done += 1
    return done


def escalate_all() -> int:
    """For the worker: every active platform tenant in turn."""
    total = 0
    for tenant in list_tenants():
        if tenant["type"] == "platform" and tenant["status"] == "active":
            with tenant_transaction(context_for(tenant["public_id"])):
                total += escalate_due()
    return total


__all__ = ["Actor", "acknowledge", "assign", "escalate_all", "escalate_due", "log_event",
           "staff_choices", "timeline"]
