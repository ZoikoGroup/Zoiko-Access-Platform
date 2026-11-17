"""Proposing and approving retention rules, and reporting what they would make due (DEV-213).

Who may act is checked by the caller (a clinic administrator, R13). Here: a rule needs a second,
different administrator before it counts; the data class must be one a module has registered; and
the report only COUNTS. There is no deletion anywhere in the product: when a period is in force the
report says how many records are past it, and a person decides what to do (decision 5 is open).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import transaction

from foundation import retention as registry
from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import RetentionRule, RuleStatus

MAX_DAYS = 36_500             # a hundred years: anything longer is "no limit" (null)


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _audit(actor: str, action: str, rule: RetentionRule) -> None:
    record(actor_ref=actor, action=f"retention.{action}", object_type="retention_rule",
           object_ref=rule.public_id, outcome=AuditOutcome.ALLOWED, reason_code=rule.data_class)


def rule_dict(r: RetentionRule) -> dict[str, Any]:
    return {"public_id": r.public_id, "data_class": r.data_class, "period_days": r.period_days,
            "basis": r.basis, "status": r.status, "proposed_by_ref": r.proposed_by_ref,
            "decided_by_ref": r.decided_by_ref,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "created_at": r.created_at.isoformat()}


def propose(*, organization_ref: str, data_class: str, period_days: Any, basis: Any,
            by: str) -> dict[str, Any]:
    if data_class not in {c.key for c in registry.data_classes()}:
        raise _bad("data_class")
    if period_days is not None and (
            isinstance(period_days, bool) or not isinstance(period_days, int)
            or not 1 <= period_days <= MAX_DAYS):
        raise _bad("period_days")
    text = " ".join(str(basis or "").split())
    if not 3 <= len(text) <= 300 or any(ord(ch) < 32 for ch in text):
        raise _bad("basis")
    with transaction.atomic():
        if RetentionRule.objects.filter(organization_ref=organization_ref, data_class=data_class,
                                        status=RuleStatus.PROPOSED, **_scope()).exists():
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        rule = RetentionRule(organization_ref=organization_ref, data_class=data_class,
                             period_days=period_days, basis=text, proposed_by_ref=by, **_scope())
        rule.save()
        _audit(by, "proposed", rule)
        return rule_dict(rule)


def _open(organization_ref: str, rule_ref: str) -> RetentionRule:
    found = RetentionRule.objects.select_for_update().filter(
        public_id=rule_ref, organization_ref=organization_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if found.status != RuleStatus.PROPOSED:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    return found


def approve(*, organization_ref: str, rule_ref: str, by: str,
            clock: Clock | None = None) -> dict[str, Any]:
    """A second administrator puts the proposal in force; the rule it replaces is superseded."""
    with transaction.atomic():
        rule = _open(organization_ref, rule_ref)
        if rule.proposed_by_ref == by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)          # two different people
        now = utc_now(clock)
        for old in RetentionRule.objects.select_for_update().filter(
                organization_ref=organization_ref, data_class=rule.data_class,
                status=RuleStatus.APPROVED, **_scope()):
            old.status = RuleStatus.SUPERSEDED
            old.save(update_fields=["status", "updated_at"])
            _audit(by, "superseded", old)
        rule.status, rule.decided_by_ref, rule.decided_at = RuleStatus.APPROVED, by, now
        rule.save(update_fields=["status", "decided_by_ref", "decided_at", "updated_at"])
        _audit(by, "approved", rule)
        return rule_dict(rule)


def withdraw(*, organization_ref: str, rule_ref: str, by: str,
             clock: Clock | None = None) -> dict[str, Any]:
    with transaction.atomic():
        rule = _open(organization_ref, rule_ref)
        rule.status, rule.decided_by_ref, rule.decided_at = (
            RuleStatus.WITHDRAWN, by, utc_now(clock))
        rule.save(update_fields=["status", "decided_by_ref", "decided_at", "updated_at"])
        _audit(by, "withdrawn", rule)
        return rule_dict(rule)


def report(organization_ref: str, clock: Clock | None = None) -> dict[str, Any]:
    """For each kind of data: the rule in force, any proposal, and how many records are past the
    period. Counts only; read-only; nothing is deleted. With no rule in force nothing is due."""
    now = utc_now(clock)
    rules = list(RetentionRule.objects.filter(
        organization_ref=organization_ref, status__in=[RuleStatus.APPROVED, RuleStatus.PROPOSED],
        **_scope()))
    held = registry.persons_on_hold()
    rows = []
    for kind in registry.data_classes():
        in_force = next((r for r in rules if r.data_class == kind.key
                         and r.status == RuleStatus.APPROVED), None)
        proposal = next((r for r in rules if r.data_class == kind.key
                         and r.status == RuleStatus.PROPOSED), None)
        days = in_force.period_days if in_force else None
        rows.append({
            "data_class": kind.key, "label": kind.label, "age_from": kind.age_from,
            "rule": rule_dict(in_force) if in_force else None,
            "proposal": rule_dict(proposal) if proposal else None,
            "past_period": kind.count(now - dt.timedelta(days=days), held) if days else None,
        })
    return {"classes": rows, "patients_on_hold": len(held), "deletes_anything": False}
