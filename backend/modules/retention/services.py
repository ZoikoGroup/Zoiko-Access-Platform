"""Proposing and approving retention rules, and reporting what they would make due (DEV-213).

Who may act is checked by the caller (a clinic administrator, R13). Here: a rule needs a second,
different administrator before it counts; the data class must be one a module has registered; and
the report COUNTS. Decided 27 Nov 2026 (DEV-398): records past a period in force are anonymised,
never deleted, and only in a batch that one administrator prepares and a different one approves.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import IntegrityError, transaction

from foundation import retention as registry
from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import BatchStatus, ErasureBatch, RetentionRule, RuleStatus

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
            # DEV-398: a period proposed for Legal to confirm, and whether a batch can anonymise.
            "suggested_days": kind.suggested_days, "can_anonymise": kind.erase is not None,
        })
    return {"classes": rows, "patients_on_hold": len(held), "deletes_anything": False,
            "batches": [batch_dict(b) for b in ErasureBatch.objects.filter(
                organization_ref=organization_ref, **_scope()).order_by("-created_at")[:20]]}


# ------------------------------------------------------------------ batches (DEV-398)
def batch_dict(b: ErasureBatch) -> dict[str, Any]:
    return {"public_id": b.public_id, "data_class": b.data_class, "cutoff": b.cutoff.isoformat(),
            "expected_count": b.expected_count, "done_count": b.done_count, "status": b.status,
            "prepared_by_ref": b.prepared_by_ref, "decided_by_ref": b.decided_by_ref,
            "decided_at": b.decided_at.isoformat() if b.decided_at else None,
            "created_at": b.created_at.isoformat()}


def _audit_batch(actor: str, action: str, batch: ErasureBatch) -> None:
    record(actor_ref=actor, action=f"retention.batch_{action}", object_type="erasure_batch",
           object_ref=batch.public_id, outcome=AuditOutcome.ALLOWED, reason_code=batch.data_class)


def prepare_batch(*, organization_ref: str, data_class: str, by: str,
                  clock: Clock | None = None) -> dict[str, Any]:
    """Fix what one round would anonymise: the period in force, a cut-off and a count. Nothing is
    changed until a different administrator approves it."""
    kind = next((c for c in registry.data_classes() if c.key == data_class), None)
    if kind is None:
        raise _bad("data_class")
    if kind.erase is None:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["not_anonymisable"])
    rule = RetentionRule.objects.filter(organization_ref=organization_ref, data_class=data_class,
                                        status=RuleStatus.APPROVED, **_scope()).first()
    if rule is None or rule.period_days is None:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["no_period_in_force"])
    cutoff = utc_now(clock) - dt.timedelta(days=rule.period_days)
    count = kind.count(cutoff, registry.persons_on_hold())
    if count == 0:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["nothing_due"])
    try:
        with transaction.atomic():
            batch = ErasureBatch.objects.create(
                organization_ref=organization_ref, data_class=data_class,
                based_on_rule=rule.public_id, cutoff=cutoff, expected_count=count,
                prepared_by_ref=by, **_scope())
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None   # one waiting batch per kind
    _audit_batch(by, "prepared", batch)
    return batch_dict(batch)


def _open_batch(organization_ref: str, batch_ref: str) -> ErasureBatch:
    found = ErasureBatch.objects.select_for_update().filter(
        public_id=batch_ref, organization_ref=organization_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if found.status != BatchStatus.PREPARED:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    return found


def approve_batch(*, organization_ref: str, batch_ref: str, by: str,
                  clock: Clock | None = None) -> dict[str, Any]:
    """A DIFFERENT administrator approves, and the batch runs at once with the cut-off fixed when
    it was prepared. People placed under a lawful hold since are still left out."""
    with transaction.atomic():
        batch = _open_batch(organization_ref, batch_ref)
        if batch.prepared_by_ref == by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)          # two different people
        kind = next((c for c in registry.data_classes() if c.key == batch.data_class), None)
        rule = RetentionRule.objects.filter(public_id=batch.based_on_rule,
                                            status=RuleStatus.APPROVED, **_scope()).first()
        if kind is None or kind.erase is None or rule is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)       # the rule changed: prepare again
        done = kind.erase(batch.cutoff, registry.persons_on_hold())
        batch.status, batch.done_count = BatchStatus.DONE, done
        batch.decided_by_ref, batch.decided_at = by, utc_now(clock)
        batch.save(update_fields=["status", "done_count", "decided_by_ref", "decided_at",
                                  "updated_at"])
        _audit_batch(by, "anonymised", batch)
        return batch_dict(batch)


def withdraw_batch(*, organization_ref: str, batch_ref: str, by: str,
                   clock: Clock | None = None) -> dict[str, Any]:
    with transaction.atomic():
        batch = _open_batch(organization_ref, batch_ref)
        batch.status, batch.decided_by_ref, batch.decided_at = (
            BatchStatus.WITHDRAWN, by, utc_now(clock))
        batch.save(update_fields=["status", "decided_by_ref", "decided_at", "updated_at"])
        _audit_batch(by, "withdrawn", batch)
        return batch_dict(batch)
