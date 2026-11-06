"""The platform's incident log: a hand-written record of what went wrong and how it ended.

A record only: it raises no alarm and fixes nothing. It must never hold patient details; the
screen says so, but the words are free text and cannot be checked here.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import Incident, IncidentSeverity, IncidentStatus
from .releases import _clean

MAX_LIST = 200


def incident_dict(row: Incident) -> dict[str, Any]:
    return {
        "public_id": row.public_id, "title": row.title, "severity": row.severity,
        "happened_on": row.happened_on.isoformat(), "details": row.details,
        "status": row.status, "resolution": row.resolution,
        "resolved_on": row.resolved_on.isoformat() if row.resolved_on else None,
        "recorded_by_ref": row.recorded_by_ref, "resolved_by_ref": row.resolved_by_ref,
        "created_at": row.created_at.isoformat(),
    }


def add_incident(
    *,
    title: object,
    severity: object,
    happened_on: object,
    details: object = "",
    actor_public_id: str,
    decision: policy.Decision,
    organization_public_id: str | None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Record one incident (starts open). The date may be today or earlier, with one day of slack
    for time zones. Needs the right to write the platform."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation="write:platform",
        organization_public_id=organization_public_id, subject_ref=None,
    )
    clean_title = _clean(title, "title", 120, required=True)
    clean_details = _clean(details, "details", 2000, required=False, multiline=True)
    if severity not in IncidentSeverity.values:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["severity"])
    if not isinstance(happened_on, dt.date) or isinstance(happened_on, dt.datetime):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["happened_on"])
    if happened_on > utc_now(clock).date() + dt.timedelta(days=1):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["happened_on"])
    row = Incident.objects.create(
        title=clean_title, severity=severity, happened_on=happened_on, details=clean_details,
        recorded_by_ref=actor_public_id)
    record(actor_ref=actor_public_id, action="incident.record", object_type="incident",
           object_ref=row.public_id, outcome=AuditOutcome.ALLOWED)
    return incident_dict(row)


def get_incident(incident_ref: str) -> dict[str, Any]:
    ctx = require_context()
    row = Incident.objects.filter(
        public_id=incident_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return incident_dict(row)


def resolve_incident(
    *,
    incident_ref: str,
    resolution: object,
    actor_public_id: str,
    decision: policy.Decision,
    organization_public_id: str | None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Close an open incident with a note on how it ended. It can be closed once; the closing
    date is the server's today, not typed by the person."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation="write:platform",
        organization_public_id=organization_public_id, subject_ref=None,
    )
    note = _clean(resolution, "resolution", 1000, required=True, multiline=True)
    ctx = require_context()
    row = Incident.objects.select_for_update().filter(
        public_id=incident_ref, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if row.status != IncidentStatus.OPEN:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    row.status, row.resolution = IncidentStatus.RESOLVED, note
    row.resolved_on, row.resolved_by_ref = utc_now(clock).date(), actor_public_id
    row.entity_version += 1
    row.save(update_fields=[
        "status", "resolution", "resolved_on", "resolved_by_ref", "entity_version", "updated_at"])
    record(actor_ref=actor_public_id, action="incident.resolve", object_type="incident",
           object_ref=row.public_id, outcome=AuditOutcome.ALLOWED)
    return incident_dict(row)


def list_incidents(limit: int = 100) -> list[dict[str, Any]]:
    """Open incidents first, then the newest date, then the newest entry. The caller has
    authorized `read:platform`."""
    ctx = require_context()
    rows = list(Incident.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id))
    rows.sort(key=lambda r: (r.status != IncidentStatus.OPEN, -r.happened_on.toordinal(),
                             -r.created_at.timestamp()))
    return [incident_dict(r) for r in rows[: max(1, min(limit, MAX_LIST))]]
