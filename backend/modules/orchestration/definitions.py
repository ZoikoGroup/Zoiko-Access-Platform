"""Workflow definitions: what path a kind of work follows, written once, then frozen (GHE-12 WF-02).

A published definition NEVER changes. A change is a new version; running instances stay on the
version they started with (WF-12: no implicit rebind). Only a clinic administrator (R13) writes
definitions, and one that traverses a clinical-review or handoff state cannot be published until
the clinical switch is turned on (WFL-06: clinical pathways stay disabled until the local operating
model, partner and country approvals exist; nothing here can see whether they do).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from django.conf import settings
from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import DefinitionStatus, WorkflowDefinition

WRITE = "write:workflow"
MANAGING_ROLES = frozenset({"R13"})
ORDER = ("requested", "validated", "accepted", "in_progress", "output_available",
         "clinical_reviewed", "handoff_acknowledged", "closed")
OMISSIBLE = frozenset({"clinical_reviewed", "handoff_acknowledged"})   # each with its own evidence
CLINICAL = OMISSIBLE
TIMER_KINDS = ("partner_response_overdue", "clinical_review_overdue", "handoff_overdue")
MAX_TIMERS = 6
_KEY = re.compile(r"^[a-z][a-z0-9-]{2,62}$")
_JOURNEY = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def check_path(path: Any) -> list[str]:
    """A path is the universal states in order. Only the review and handoff states may be left out;
    everything else is mandatory, so no definition can skip evidence the spec insists on."""
    if not isinstance(path, list) or any(not isinstance(s, str) for s in path):
        raise _bad("path")
    if len(set(path)) != len(path) or any(s not in ORDER for s in path):
        raise _bad("path")
    required = [s for s in ORDER if s not in OMISSIBLE]
    if any(s not in path for s in required):
        raise _bad("path")
    if path != [s for s in ORDER if s in path]:           # in the universal order
        raise _bad("path")
    return list(path)


def check_timers(policies: Any, path: list[str]) -> list[dict[str, Any]]:
    """No universal deadline exists (GHE-12 section 06): each timer states its own minutes."""
    if policies in (None, []):
        return []
    if not isinstance(policies, list) or len(policies) > MAX_TIMERS:
        raise _bad("timer_policies")
    clean = []
    for item in policies:
        if not isinstance(item, dict) or set(item) != {"state", "kind", "after_minutes"}:
            raise _bad("timer_policies")
        minutes = item["after_minutes"]
        if (item["state"] not in path or item["state"] == "closed"
                or item["kind"] not in TIMER_KINDS or isinstance(minutes, bool)
                or not isinstance(minutes, int) or not 1 <= minutes <= 525_600):
            raise _bad("timer_policies")
        clean.append({"state": item["state"], "kind": item["kind"], "after_minutes": minutes})
    return sorted(clean, key=lambda t: (ORDER.index(t["state"]), t["kind"]))


def fingerprint(key: str, version: int, journey: str, path: list[str],
                policies: list[dict[str, Any]]) -> str:
    body = {"key": key, "version": version, "journey": journey, "path": path, "timers": policies}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
                          ).hexdigest()


def definition_dict(d: WorkflowDefinition) -> dict[str, Any]:
    return {
        "public_id": d.public_id, "key": d.key, "version": d.version, "journey": d.journey,
        "path": list(d.path), "timer_policies": list(d.timer_policies), "status": d.status,
        "content_hash": d.content_hash, "created_at": d.created_at.isoformat(),
        "clinical": bool(set(d.path) & CLINICAL),
    }


def _managed(decision: policy.Decision, actor: str, org: str | None) -> None:
    policy.check_receipt(decision, actor_public_id=actor, operation=WRITE,
                         organization_public_id=org)
    if decision.role_code not in MANAGING_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


def _own(ref: str) -> WorkflowDefinition:
    found = WorkflowDefinition.objects.select_for_update().filter(
        public_id=ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def create_definition(
    *, key: str, journey: str, path: Any, timer_policies: Any, actor_public_id: str,
    decision: policy.Decision, organization_ref: str | None,
) -> dict[str, Any]:
    _managed(decision, actor_public_id, organization_ref)
    if not _KEY.match(key or ""):
        raise _bad("key")
    if not _JOURNEY.match(journey or ""):
        raise _bad("journey")
    clean_path = check_path(path)
    policies = check_timers(timer_policies, clean_path)
    with transaction.atomic():
        if WorkflowDefinition.objects.filter(key=key, **_scope()).exists():
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row = WorkflowDefinition(
            key=key, version=1, journey=journey, path=clean_path, timer_policies=policies,
            created_by_ref=actor_public_id,
            content_hash=fingerprint(key, 1, journey, clean_path, policies), **_scope())
        row.save()
        record(actor_ref=actor_public_id, action="orchestration.definition_created",
               object_type="workflow_definition", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
        return definition_dict(row)


def edit_definition(
    *, definition_ref: str, path: Any, timer_policies: Any, actor_public_id: str,
    decision: policy.Decision, organization_ref: str | None,
) -> dict[str, Any]:
    """Only a draft can change; a published definition is frozen."""
    _managed(decision, actor_public_id, organization_ref)
    clean_path = check_path(path)
    policies = check_timers(timer_policies, clean_path)
    with transaction.atomic():
        row = _own(definition_ref)
        if row.status != DefinitionStatus.DRAFT:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.path, row.timer_policies = clean_path, policies
        row.content_hash = fingerprint(row.key, row.version, row.journey, clean_path, policies)
        row.entity_version += 1
        row.save(update_fields=["path", "timer_policies", "content_hash", "entity_version",
                                "updated_at"])
        record(actor_ref=actor_public_id, action="orchestration.definition_edited",
               object_type="workflow_definition", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
        return definition_dict(row)


def publish_definition(
    *, definition_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    _managed(decision, actor_public_id, organization_ref)
    with transaction.atomic():
        row = _own(definition_ref)
        if row.status == DefinitionStatus.PUBLISHED:
            return definition_dict(row)                      # repeating it is harmless
        if row.status != DefinitionStatus.DRAFT:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if set(row.path) & CLINICAL and not getattr(settings, "WORKFLOW_CLINICAL_ENABLED", False):
            raise ZoikoError(ErrorCode.POLICY_DENIED)         # clinical pathways stay off
        row.status = DefinitionStatus.PUBLISHED
        row.published_by_ref = actor_public_id
        row.entity_version += 1
        row.save(update_fields=["status", "published_by_ref", "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="orchestration.definition_published",
               object_type="workflow_definition", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
        return definition_dict(row)


def new_version(
    *, definition_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """A change to a published definition: a new draft version. Running instances stay put."""
    _managed(decision, actor_public_id, organization_ref)
    with transaction.atomic():
        old = _own(definition_ref)
        if old.status not in (DefinitionStatus.PUBLISHED, DefinitionStatus.RETIRED):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        latest = WorkflowDefinition.objects.filter(key=old.key, **_scope()).order_by(
            "-version").first()
        if latest is None or latest.status == DefinitionStatus.DRAFT:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)      # one draft at a time
        row = WorkflowDefinition(
            key=old.key, version=latest.version + 1, journey=old.journey, path=old.path,
            timer_policies=old.timer_policies, created_by_ref=actor_public_id, **_scope())
        row.content_hash = fingerprint(row.key, row.version, row.journey, row.path,
                                       row.timer_policies)
        row.save()
        record(actor_ref=actor_public_id, action="orchestration.definition_versioned",
               object_type="workflow_definition", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
        return definition_dict(row)


def retire_definition(
    *, definition_ref: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """No new instances start on it. Running ones carry on and stay readable."""
    _managed(decision, actor_public_id, organization_ref)
    with transaction.atomic():
        row = _own(definition_ref)
        if row.status == DefinitionStatus.RETIRED:
            return definition_dict(row)
        if row.status != DefinitionStatus.PUBLISHED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.status = DefinitionStatus.RETIRED
        row.entity_version += 1
        row.save(update_fields=["status", "entity_version", "updated_at"])
        record(actor_ref=actor_public_id, action="orchestration.definition_retired",
               object_type="workflow_definition", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
        return definition_dict(row)


def list_definitions() -> list[dict[str, Any]]:
    rows = WorkflowDefinition.objects.filter(**_scope()).order_by("key", "-version")
    return [definition_dict(r) for r in rows]
