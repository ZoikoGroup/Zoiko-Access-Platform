"""Recording and ending a patient's insurance coverage.

Writes need a policy RECEIPT for `write:coverage` for exactly this actor, organization and patient.
Reads are for callers that already passed `read:coverage` for each patient. The member id is never
returned in full: lists show only its last four characters, which is enough to recognise a card.
Audit entries say who added or ended a coverage, never what it contains.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.crypto import encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .models import Coverage, CoverageStatus

WRITE_COVERAGE = "write:coverage"
MAX_ACTIVE_PER_PERSON = 5
_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-/]{2,39}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _audit(by: str, action: str, ref: str) -> None:
    record(actor_ref=by, action=action, object_type="coverage", object_ref=ref,
           outcome=AuditOutcome.ALLOWED)


def _own(coverage_ref: str) -> Coverage:
    found = Coverage.objects.filter(public_id=coverage_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def _text(value: str, field: str, low: int, high: int) -> str:
    value = " ".join((value or "").split())
    if not low <= len(value) <= high or any(ord(ch) < 32 for ch in value):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return value


def _date(value: str, field: str) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field]) from None
    if not dt.date(1990, 1, 1) <= parsed <= dt.date(2100, 12, 31):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return parsed


def coverage_dict(c: Coverage, today: dt.date) -> dict[str, Any]:
    """Safe to show: the member id appears only as its last four characters."""
    expired = c.valid_until is not None and c.valid_until < today
    return {
        "public_id": c.public_id, "person_ref": c.person_ref, "insurer_name": c.insurer_name,
        "plan_name": c.plan_name, "member_last4": c.member_last4,
        "valid_from": c.valid_from.isoformat() if c.valid_from else None,
        "valid_until": c.valid_until.isoformat() if c.valid_until else None,
        "status": c.status,
        "in_force": c.status == CoverageStatus.ACTIVE and not expired,
        "created_at": c.created_at.isoformat(),
    }


def add_coverage(
    *,
    person_ref: str,
    insurer_name: str,
    plan_name: str,
    member_id: str,
    valid_from: str,
    valid_until: str,
    actor_public_id: str,
    decision: policy.Decision,
    organization_public_id: str | None = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    ctx = require_context()
    today = utc_now(clock).date()
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_COVERAGE,
        organization_public_id=organization_public_id, subject_ref=person_ref)
    if not trust.person_exists(person_ref):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    insurer = _text(insurer_name, "insurer", 2, 80)
    plan = _text(plan_name, "plan", 0, 80) if (plan_name or "").strip() else ""
    member = (member_id or "").strip()
    if not _MEMBER.match(member):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["member_id"])
    start, end = _date(valid_from, "valid_from"), _date(valid_until, "valid_until")
    if start and end and end < start:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["valid_until"])
    with transaction.atomic():
        active = Coverage.objects.filter(
            person_ref=person_ref, status=CoverageStatus.ACTIVE, **_scope()).count()
        if active >= MAX_ACTIVE_PER_PERSON:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)       # end an old one first
        row = Coverage(
            person_ref=person_ref, insurer_name=insurer, plan_name=plan,
            member_last4=member[-4:], valid_from=start, valid_until=end,
            added_by_ref=actor_public_id, member_ref_enc="", **_scope())
        # The sealed value is bound to this very row, so it cannot be moved to another coverage.
        row.member_ref_enc = encrypt(
            member, tenant_id=ctx.tenant_id, context=f"coverage:{row.public_id}:member_ref")
        row.save()
        _audit(actor_public_id, "coverage.add", row.public_id)
        return coverage_dict(row, today)


def end_coverage(
    *,
    coverage_ref: str,
    actor_public_id: str,
    decision: policy.Decision,
    organization_public_id: str | None = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    today = utc_now(clock).date()
    with transaction.atomic():
        row = _own(coverage_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_COVERAGE,
            organization_public_id=organization_public_id, subject_ref=row.person_ref)
        if row.status == CoverageStatus.ENDED:
            return coverage_dict(row, today)                    # repeating is harmless
        row.status = CoverageStatus.ENDED
        row.ended_by_ref = actor_public_id
        row.entity_version += 1
        row.save(update_fields=["status", "ended_by_ref", "entity_version", "updated_at"])
        _audit(actor_public_id, "coverage.end", row.public_id)
        return coverage_dict(row, today)


def get_coverage(coverage_ref: str, *, clock: Clock | None = None) -> dict[str, Any]:
    return coverage_dict(_own(coverage_ref), utc_now(clock).date())


def list_coverage(
    person_refs: list[str], *, include_ended: bool = False, clock: Clock | None = None
) -> list[dict[str, Any]]:
    """Newest first. The caller has already been allowed to read each of these patients."""
    if not person_refs:
        return []
    today = utc_now(clock).date()
    rows = Coverage.objects.filter(person_ref__in=person_refs, **_scope())
    if not include_ended:
        rows = rows.filter(status=CoverageStatus.ACTIVE)
    return [coverage_dict(c, today) for c in rows.order_by("-created_at")[:200]]
