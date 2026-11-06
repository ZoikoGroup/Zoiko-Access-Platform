"""The platform's release log: a hand-written record of what went live and when.

Counts as evidence for a release review, not as control: nothing here deploys anything.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import ReleaseNote

MAX_LIST = 200
_BAD = {chr(c) for c in range(32)} | {chr(127)}


def _clean(
    value: object, field: str, limit: int, *, required: bool, multiline: bool = False
) -> str:
    """Trimmed text of at most `limit` characters; control characters are refused (line breaks are
    allowed in `multiline` fields only)."""
    if not isinstance(value, str):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    text = value.strip()
    allowed = {"\n", "\t"} if multiline else set()
    if (required and not text) or len(text) > limit or any(
            c in _BAD and c not in allowed for c in text):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return text


def release_dict(note: ReleaseNote) -> dict[str, Any]:
    return {
        "public_id": note.public_id, "version": note.version, "summary": note.summary,
        "details": note.details, "released_on": note.released_on.isoformat(),
        "recorded_by_ref": note.recorded_by_ref, "created_at": note.created_at.isoformat(),
    }


def add_release(
    *,
    version: object,
    summary: object,
    released_on: object,
    details: object = "",
    actor_public_id: str,
    decision: policy.Decision,
    organization_public_id: str | None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Record one release. A version can be recorded once; the date may be today or earlier (one
    day of slack for time zones). Needs the right to write the platform."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation="write:platform",
        organization_public_id=organization_public_id, subject_ref=None,
    )
    clean_version = _clean(version, "version", 40, required=True)
    clean_summary = _clean(summary, "summary", 200, required=True)
    clean_details = _clean(details, "details", 2000, required=False, multiline=True)
    if not isinstance(released_on, dt.date) or isinstance(released_on, dt.datetime):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["released_on"])
    if released_on > utc_now(clock).date() + dt.timedelta(days=1):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["released_on"])
    ctx = require_context()
    if ReleaseNote.objects.filter(
            cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, version=clean_version).exists():
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    note = ReleaseNote.objects.create(
        version=clean_version, summary=clean_summary, details=clean_details,
        released_on=released_on, recorded_by_ref=actor_public_id)
    record(actor_ref=actor_public_id, action="release.record", object_type="release",
           object_ref=note.public_id, outcome=AuditOutcome.ALLOWED)
    return release_dict(note)


def list_releases(limit: int = 100) -> list[dict[str, Any]]:
    """Newest release date first (then newest entry). The caller has authorized `read:platform`."""
    ctx = require_context()
    rows = ReleaseNote.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).order_by(
        "-released_on", "-created_at")[: max(1, min(limit, MAX_LIST))]
    return [release_dict(r) for r in rows]
