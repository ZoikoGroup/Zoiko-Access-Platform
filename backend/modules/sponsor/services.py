"""Employer programme counts and payer plans, members and entitlement checks (TEMPORARY, DEV-159).

Employer rules
- The employer enrols people by an opaque reference and never sees a list of them back: the only
  thing read is a COUNT, and a count of 1 up to one less than `SPONSOR_MIN_GROUP` is hidden, so a
  small group cannot point at a person (GHE-17, GHE-16 TC55). Zero is shown as zero.
Payer rules
- A plan names the service categories it covers and the patient's share. A check answers "covered"
  or says exactly why not; every check is audited against the member record, never with the
  member number in the audit entry.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import CoveragePlan, MemberState, PlanMember, ProgramMember

MAX_BATCH = 200
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,39}$")
_PLAN_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,19}$")
_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def min_group() -> int:
    return int(getattr(settings, "SPONSOR_MIN_GROUP", 10))


def _receipt(decision: policy.Decision, actor: str, operation: str, org: str) -> None:
    policy.check_receipt(decision, actor_public_id=actor, operation=operation,
                         organization_public_id=org)


def _audit(actor: str, action: str, object_type: str, object_ref: str, **details: int) -> None:
    record(actor_ref=actor, action=action, object_type=object_type, object_ref=object_ref,
           outcome=AuditOutcome.ALLOWED, details=details or None)


def _refs(value: Any, field: str) -> list[str]:
    if (not isinstance(value, list) or not 1 <= len(value) <= MAX_BATCH
            or any(not isinstance(v, str) or not _REF.match(v) for v in value)):
        raise _bad(field)
    return list(dict.fromkeys(value))


# ------------------------------------------------------------------ employer: enrol and count
def add_members(
    *, organization_ref: str, refs: Any, actor_public_id: str, decision: policy.Decision,
) -> dict[str, int]:
    _receipt(decision, actor_public_id, "write:program", organization_ref)
    clean = _refs(refs, "refs")
    added = again = 0
    with transaction.atomic():
        have = {m.member_ref: m for m in ProgramMember.objects.select_for_update().filter(
            organization_ref=organization_ref, member_ref__in=clean, **_scope())}
        for ref in clean:
            row = have.get(ref)
            if row is None:
                ProgramMember.objects.create(
                    organization_ref=organization_ref, member_ref=ref,
                    added_by_ref=actor_public_id, **_scope())
                added += 1
            elif row.state == MemberState.WITHDRAWN:
                row.state = MemberState.ACTIVE
                row.entity_version += 1
                row.save(update_fields=["state", "entity_version", "updated_at"])
                added += 1
            else:
                again += 1
        _audit(actor_public_id, "program.add_members", "organization", organization_ref,
               added=added)
    return {"added": added, "already_enrolled": again}


def withdraw_members(
    *, organization_ref: str, refs: Any, actor_public_id: str, decision: policy.Decision,
) -> dict[str, int]:
    _receipt(decision, actor_public_id, "write:program", organization_ref)
    clean = _refs(refs, "refs")
    with transaction.atomic():
        rows = ProgramMember.objects.select_for_update().filter(
            organization_ref=organization_ref, member_ref__in=clean, state=MemberState.ACTIVE,
            **_scope())
        done = 0
        for row in rows:
            row.state = MemberState.WITHDRAWN
            row.entity_version += 1
            row.save(update_fields=["state", "entity_version", "updated_at"])
            done += 1
        _audit(actor_public_id, "program.withdraw_members", "organization", organization_ref,
               withdrawn=done)
    return {"withdrawn": done}


def _shown(count: int, k: int) -> dict[str, Any]:
    hidden = 0 < count < k
    return {"value": None if hidden else count, "hidden": hidden}


def program_summary(
    *, organization_ref: str, actor_public_id: str, decision: policy.Decision,
) -> dict[str, Any]:
    """Counts only. A group smaller than the minimum is hidden, never shown as a small number."""
    _receipt(decision, actor_public_id, "read:program", organization_ref)
    k = min_group()
    base = ProgramMember.objects.filter(organization_ref=organization_ref, **_scope())
    return {"min_group": k,
            "enrolled": _shown(base.filter(state=MemberState.ACTIVE).count(), k),
            "withdrawn": _shown(base.filter(state=MemberState.WITHDRAWN).count(), k)}


def payer_summary(*, organization_ref: str, actor_public_id: str, decision: policy.Decision
                  ) -> dict[str, int]:
    """Numbers only, for the payer's home screen."""
    _receipt(decision, actor_public_id, "read:coverage", organization_ref)
    plans = CoveragePlan.objects.filter(organization_ref=organization_ref, **_scope())
    members = PlanMember.objects.filter(organization_ref=organization_ref, **_scope())
    return {"plans": plans.count(), "plans_on": plans.filter(active=True).count(),
            "members": members.filter(state=MemberState.ACTIVE).count(),
            "members_ended": members.filter(state=MemberState.WITHDRAWN).count()}


# ------------------------------------------------------------------ payer: plans and members
def plan_dict(p: CoveragePlan) -> dict[str, Any]:
    return {"id": p.public_id, "code": p.code, "name": p.name,
            "covered_categories": list(p.covered_categories), "copay_percent": p.copay_percent,
            "active": p.active}


def _categories(value: Any) -> list[str]:
    if (not isinstance(value, list) or not 1 <= len(value) <= 20
            or any(not isinstance(v, str) or not _CATEGORY.match(v) for v in value)):
        raise _bad("covered_categories")
    return sorted(dict.fromkeys(value))


def _copay(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise _bad("copay_percent")
    return value


def create_plan(
    *, organization_ref: str, code: Any, name: Any, covered_categories: Any, copay_percent: Any,
    actor_public_id: str, decision: policy.Decision,
) -> dict[str, Any]:
    _receipt(decision, actor_public_id, "write:coverage", organization_ref)
    if not isinstance(code, str) or not _PLAN_CODE.match(code):
        raise _bad("code")
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 80:
        raise _bad("name")
    cats, share = _categories(covered_categories), _copay(copay_percent)
    try:
        with transaction.atomic():
            row = CoveragePlan.objects.create(
                organization_ref=organization_ref, code=code, name=name.strip(),
                covered_categories=cats, copay_percent=share, created_by_ref=actor_public_id,
                **_scope())
            _audit(actor_public_id, "plan.create", "coverage_plan", row.public_id)
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
    return plan_dict(row)


def update_plan(
    *, plan_ref: str, organization_ref: str, covered_categories: Any = None,
    copay_percent: Any = None, active: Any = None, actor_public_id: str,
    decision: policy.Decision,
) -> dict[str, Any]:
    _receipt(decision, actor_public_id, "write:coverage", organization_ref)
    with transaction.atomic():
        row = CoveragePlan.objects.select_for_update().filter(
            public_id=plan_ref, organization_ref=organization_ref, **_scope()).first()
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if covered_categories is not None:
            row.covered_categories = _categories(covered_categories)
        if copay_percent is not None:
            row.copay_percent = _copay(copay_percent)
        if active is not None:
            if not isinstance(active, bool):
                raise _bad("active")
            row.active = active
        row.entity_version += 1
        row.save()
        _audit(actor_public_id, "plan.update", "coverage_plan", row.public_id)
        return plan_dict(row)


def list_plans(*, organization_ref: str, actor_public_id: str, decision: policy.Decision
               ) -> list[dict[str, Any]]:
    _receipt(decision, actor_public_id, "read:coverage", organization_ref)
    rows = CoveragePlan.objects.filter(organization_ref=organization_ref, **_scope())
    return [plan_dict(p) for p in rows.order_by("code")]


def _date(value: Any, field: str) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise _bad(field) from None


def _mask(member_id: str) -> str:
    return "…" + member_id[-4:]


def member_dict(m: PlanMember) -> dict[str, Any]:
    return {"id": m.public_id, "member": _mask(m.member_id), "plan": m.plan.code,
            "valid_from": m.valid_from.isoformat() if m.valid_from else None,
            "valid_until": m.valid_until.isoformat() if m.valid_until else None, "state": m.state}


def add_plan_member(
    *, organization_ref: str, plan_code: Any, member_id: Any, valid_from: Any = None,
    valid_until: Any = None, actor_public_id: str, decision: policy.Decision,
) -> dict[str, Any]:
    _receipt(decision, actor_public_id, "write:coverage", organization_ref)
    if not isinstance(member_id, str) or not _REF.match(member_id):
        raise _bad("member_id")
    start, end = _date(valid_from, "valid_from"), _date(valid_until, "valid_until")
    if start and end and end < start:
        raise _bad("valid_until")
    try:
        with transaction.atomic():
            plan = CoveragePlan.objects.filter(
                code=plan_code, organization_ref=organization_ref, **_scope()).first()
            if plan is None:
                raise _bad("plan")
            row = PlanMember.objects.create(
                organization_ref=organization_ref, plan=plan, member_id=member_id,
                valid_from=start, valid_until=end, added_by_ref=actor_public_id, **_scope())
            _audit(actor_public_id, "plan.add_member", "plan_member", row.public_id)
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
    return member_dict(row)


def end_plan_member(
    *, member_ref: str, organization_ref: str, actor_public_id: str, decision: policy.Decision,
) -> dict[str, Any]:
    _receipt(decision, actor_public_id, "write:coverage", organization_ref)
    with transaction.atomic():
        row = PlanMember.objects.select_for_update().select_related("plan").filter(
            public_id=member_ref, organization_ref=organization_ref, **_scope()).first()
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.state != MemberState.WITHDRAWN:
            row.state = MemberState.WITHDRAWN
            row.entity_version += 1
            row.save(update_fields=["state", "entity_version", "updated_at"])
            _audit(actor_public_id, "plan.end_member", "plan_member", row.public_id)
        return member_dict(row)


def list_plan_members(*, organization_ref: str, actor_public_id: str, decision: policy.Decision
                      ) -> list[dict[str, Any]]:
    _receipt(decision, actor_public_id, "read:coverage", organization_ref)
    rows = PlanMember.objects.select_related("plan").filter(
        organization_ref=organization_ref, **_scope())
    return [member_dict(m) for m in rows.order_by("-created_at")[:200]]


def check_entitlement(
    *, organization_ref: str, member_id: Any, category: Any, on: Any = None,
    actor_public_id: str, decision: policy.Decision, clock: Clock | None = None,
) -> dict[str, Any]:
    """Is this member covered for this kind of service on this day? Always an answer, with a
    reason."""
    _receipt(decision, actor_public_id, "read:coverage", organization_ref)
    if not isinstance(member_id, str) or not _REF.match(member_id):
        raise _bad("member_id")
    if not isinstance(category, str) or not _CATEGORY.match(category):
        raise _bad("category")
    day = _date(on, "on") or utc_now(clock).date()
    member = PlanMember.objects.select_related("plan").filter(
        organization_ref=organization_ref, member_id=member_id, **_scope()).first()
    if member is None:
        reason, plan = "no_member", None
    elif member.state != MemberState.ACTIVE:
        reason, plan = "member_ended", member.plan
    elif ((member.valid_from and day < member.valid_from)
          or (member.valid_until and day > member.valid_until)):
        reason, plan = "outside_dates", member.plan
    elif not member.plan.active:
        reason, plan = "plan_inactive", member.plan
    elif category not in member.plan.covered_categories:
        reason, plan = "not_covered", member.plan
    else:
        reason, plan = "covered", member.plan
    with transaction.atomic():
        _audit(actor_public_id, "plan.check", "plan_member",
               member.public_id if member else organization_ref, covered=int(reason == "covered"))
    return {"covered": reason == "covered", "reason": reason,
            "plan": plan.code if plan else None,
            "copay_percent": plan.copay_percent if plan and reason == "covered" else None}
