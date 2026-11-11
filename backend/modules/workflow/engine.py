"""The workflow engine (GHE-12): durable journeys as explicit state machines with evidence.

`WorkflowEngine` is the interface the rest of the product depends on; `DbWorkflowEngine` is the
default implementation, backed by the workflow tables and run by the worker. A Temporal-class
engine can replace it later without touching the modules that use it (architecture section 6).

Every change is ONE transaction (architecture section 5): lock the instance, compare its version,
check the guards and the evidence, move the state, append the chained transition, start or cancel
timers, record the audit event and queue the outbox event. Nothing outside the database is called.

Rules that hold on every call
- An instance runs on the definition version it started on, forever.
- A transition needs the right EVIDENCE from the right KIND of actor: a technical receipt, a
  viewed screen, a clinician's acknowledgment and the source's own confirmation are four different
  things, and a system process can never stand in for a clinician or for the source.
- Two people acting at once cannot both win: the caller states the version it saw, and a stale one
  is refused (CONFLICT_VERSION).
- A timer only raises an exception for a person to look at. It never moves the journey, and a timer
  that fires after the journey ended (or left the state that started it) does nothing.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from django.conf import settings
from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, TimeError, get_zone, utc_now
from modules.evidence.api import AuditOutcome, record

from .definitions import (
    ACTOR_KINDS,
    EVIDENCE_ACTORS,
    EVIDENCE_KINDS,
    digest_of,
    validate_definition,
)
from .models import (
    DefinitionState,
    ExceptionStatus,
    InstanceStatus,
    TimerStatus,
    WorkflowDefinition,
    WorkflowException,
    WorkflowInstance,
    WorkflowTimer,
    WorkflowTransition,
)

_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
BUNDLED = Path(__file__).with_name("bundled")


@dataclass(frozen=True)
class Evidence:
    """A pointer to evidence that already exists elsewhere (a receipt, an acknowledgment...)."""

    kind: str
    ref: str


class WorkflowEngine(Protocol):
    def start(
        self, *, definition_key: str, subject_ref: str, owner_ref: str, actor_ref: str,
        organization_ref: str = "", zone: str = "UTC", version: int | None = None,
        clock: Clock | None = None,
    ) -> dict[str, Any]: ...

    def submit_transition(
        self, *, instance_ref: str, transition: str, actor_ref: str, actor_kind: str,
        expected_version: int, evidence: list[Evidence] | None = None, clock: Clock | None = None,
    ) -> dict[str, Any]: ...

    def fire_due_timers(self, *, clock: Clock | None = None, limit: int = 100) -> int: ...

    def get_instance(self, instance_ref: str) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- definitions


def definition_dict(row: WorkflowDefinition) -> dict[str, Any]:
    body = row.body
    return {"public_id": row.public_id, "key": row.key, "version": row.version,
            "state": row.state, "body_digest": row.body_digest,
            "released_at": row.released_at.isoformat(), "initial": body.get("initial", ""),
            "states": list(body.get("states", {})),
            "terminal": dict(body.get("terminal", {})),
            "transitions": list(body.get("transitions", {})),
            "timers": {n: s.get("after_minutes") for n, s in body.get("timers", {}).items()}}


def publish_definition(body: Any, *, clock: Clock | None = None) -> dict[str, Any]:
    """Validate a definition and release it. The same key and version may be published again only
    if the content is byte-for-byte the same: a released version never changes."""
    validate_definition(body)
    digest = digest_of(body)
    cell = settings.ZOIKO_CELL_ID
    existing = WorkflowDefinition.objects.filter(
        cell_id=cell, key=body["key"], version=body["version"]).first()
    if existing is not None:
        if existing.body_digest != digest:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        return definition_dict(existing)
    try:
        with transaction.atomic():
            row = WorkflowDefinition.objects.create(
                cell_id=cell, key=body["key"], version=body["version"], body=body,
                body_digest=digest, released_at=utc_now(clock))
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
    return definition_dict(row)


def retire_definition(key: str, version: int) -> dict[str, Any]:
    """Stop new instances of this version. Instances already running carry on."""
    row = WorkflowDefinition.objects.filter(
        cell_id=settings.ZOIKO_CELL_ID, key=key, version=version).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    row.state = DefinitionState.RETIRED
    row.save(update_fields=["state"])
    return definition_dict(row)


def load_bundled_definitions(*, clock: Clock | None = None) -> list[dict[str, Any]]:
    """Publish every definition shipped in `bundled/`. Safe to repeat."""
    return [
        publish_definition(json.loads(path.read_text(encoding="utf8")), clock=clock)
        for path in sorted(BUNDLED.glob("*.json"))
    ]


def list_definitions() -> list[dict[str, Any]]:
    rows = WorkflowDefinition.objects.filter(cell_id=settings.ZOIKO_CELL_ID)
    return [definition_dict(r) for r in rows.order_by("key", "-version")]


def _released(key: str, version: int | None) -> WorkflowDefinition:
    rows = WorkflowDefinition.objects.filter(
        cell_id=settings.ZOIKO_CELL_ID, key=key, state=DefinitionState.RELEASED)
    row = (rows.filter(version=version).first() if version is not None
           else rows.order_by("-version").first())
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


# --------------------------------------------------------------------------- helpers


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def instance_dict(i: WorkflowInstance) -> dict[str, Any]:
    return {
        "public_id": i.public_id, "definition_key": i.definition.key,
        "definition_version": i.definition.version, "subject_ref": i.subject_ref,
        "organization_ref": i.organization_ref, "state": i.state, "status": i.status,
        "owner_ref": i.owner_ref, "zone": i.zone, "entity_version": i.entity_version,
        "completed_at": i.completed_at.isoformat() if i.completed_at else None,
    }


def _evidence_text(evidence: list[dict[str, str]]) -> str:
    """The evidence in one fixed layout. PostgreSQL's jsonb reorders object keys, so the digest
    must never depend on the order they come back in."""
    return json.dumps(evidence, sort_keys=True, separators=(",", ":"))


def _parts(row: WorkflowTransition) -> list[Any]:
    return [row.instance.public_id, row.transition, row.from_state, row.to_state, row.actor_ref,
            row.actor_kind, _evidence_text(row.evidence), row.occurred_at.isoformat()]


def _append(
    instance: WorkflowInstance, transition: str, from_state: str, actor_ref: str, actor_kind: str,
    evidence: list[dict[str, str]], now: dt.datetime,
) -> None:
    last = WorkflowTransition.objects.filter(
        instance=instance, **_scope()).order_by("-seq").first()
    seq = last.seq + 1 if last else 1
    prev = last.record_digest if last else GENESIS
    digest = chain_digest(prev, seq, instance.public_id, transition, from_state, instance.state,
                          actor_ref, actor_kind, _evidence_text(evidence), now.isoformat())
    try:
        with transaction.atomic():
            WorkflowTransition.objects.create(
                instance=instance, seq=seq, transition=transition, from_state=from_state,
                to_state=instance.state, actor_ref=actor_ref, actor_kind=actor_kind,
                evidence=evidence, occurred_at=now, prev_digest=prev, record_digest=digest)
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None


def _start_timers(instance: WorkflowInstance, body: dict[str, Any], state: str,
                  now: dt.datetime) -> None:
    for name, spec in body.get("timers", {}).items():
        if spec["state"] == state:
            WorkflowTimer.objects.create(
                instance=instance, name=name, state_name=state, zone=instance.zone,
                deadline_at=now + dt.timedelta(minutes=spec["after_minutes"]), **_scope())


def _cancel_timers(instance: WorkflowInstance, state: str | None = None) -> None:
    pending = WorkflowTimer.objects.filter(
        instance=instance, status=TimerStatus.PENDING, **_scope())
    if state is not None:
        pending = pending.filter(state_name=state)
    pending.update(status=TimerStatus.CANCELLED)


def _missing_evidence(kind: str) -> ZoikoError:
    """The error that says what is missing, in the product's own words (GHE-11 W17)."""
    if kind == "professional_acknowledgment":
        return ZoikoError(ErrorCode.CLINICAL_ACCEPTANCE_ABSENT)
    if kind == "source_confirmed_outcome":
        return ZoikoError(ErrorCode.PARTNER_UNCONFIRMED)
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["evidence"])


def _check_evidence(
    spec: dict[str, Any], actor_kind: str, evidence: list[Evidence]
) -> list[dict[str, str]]:
    for item in evidence:
        if item.kind not in EVIDENCE_KINDS or not _REF.match(item.ref):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["evidence"])
        if actor_kind not in EVIDENCE_ACTORS[item.kind]:
            raise _missing_evidence(item.kind)      # e.g. a system "acknowledging" for a clinician
    supplied = {item.kind for item in evidence}
    for needed in spec.get("requires_evidence", []):
        if needed not in supplied:                  # a receipt never stands in for anything else
            raise _missing_evidence(needed)
    return [{"kind": e.kind, "ref": e.ref} for e in evidence]


# --------------------------------------------------------------------------- the engine


class DbWorkflowEngine:
    """Default engine, backed by the workflow tables."""

    def start(
        self, *, definition_key: str, subject_ref: str, owner_ref: str, actor_ref: str,
        organization_ref: str = "", zone: str = "UTC", version: int | None = None,
        clock: Clock | None = None,
    ) -> dict[str, Any]:
        for value in (subject_ref, owner_ref, actor_ref):
            if not _REF.match(value):
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["ref"])
        try:
            get_zone(zone)
        except TimeError:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["zone"]) from None
        definition = _released(definition_key, version)
        initial = definition.body["initial"]
        now = utc_now(clock)
        try:
            with transaction.atomic():
                instance = WorkflowInstance.objects.create(
                    definition=definition, subject_ref=subject_ref, state=initial,
                    organization_ref=organization_ref, owner_ref=owner_ref, zone=zone,
                    **_scope())
                _append(instance, "start", "", actor_ref, "system", [], now)
                _start_timers(instance, definition.body, initial, now)
                record(actor_ref=actor_ref, action="workflow.start",
                       object_type="workflow_instance", object_ref=instance.public_id,
                       outcome=AuditOutcome.ALLOWED)
                emit(event_type="workflow.instance.started.v1", aggregate_type="workflow_instance",
                     aggregate_ref=instance.public_id,
                     payload={"instance_ref": instance.public_id, "definition_key": definition.key,
                              "definition_version": definition.version, "state": initial})
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None      # already running
        return instance_dict(instance)

    def submit_transition(
        self, *, instance_ref: str, transition: str, actor_ref: str, actor_kind: str,
        expected_version: int, evidence: list[Evidence] | None = None, clock: Clock | None = None,
    ) -> dict[str, Any]:
        if actor_kind not in ACTOR_KINDS or not _REF.match(actor_ref):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["actor"])
        now = utc_now(clock)
        with transaction.atomic():
            instance = (WorkflowInstance.objects.select_for_update(of=("self",))
                        .select_related("definition")
                        .filter(public_id=instance_ref, **_scope()).first())
            if instance is None:
                raise ZoikoError(ErrorCode.NOT_FOUND)
            if instance.entity_version != expected_version:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)
            if instance.status != InstanceStatus.RUNNING:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)            # the journey has ended
            body = instance.definition.body             # the version it started on, always
            spec = body["transitions"].get(transition)
            if spec is None or instance.state not in spec["from"]:
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["transition"])
            for guard in spec.get("guards", []):
                if guard == "actor_is_owner" and actor_ref != instance.owner_ref:
                    raise ZoikoError(ErrorCode.POLICY_DENIED)
                if guard == "human_actor" and actor_kind in ("system", "partner"):
                    raise ZoikoError(ErrorCode.POLICY_DENIED)
            kept = _check_evidence(spec, actor_kind, evidence or [])

            old_state = instance.state
            instance.state = spec["to"]
            instance.entity_version += 1
            outcome = body["terminal"].get(instance.state)
            fields = ["state", "entity_version", "updated_at"]
            if outcome is not None:
                instance.status = outcome
                instance.completed_at = now
                fields += ["status", "completed_at"]
            instance.save(update_fields=fields)
            _append(instance, transition, old_state, actor_ref, actor_kind, kept, now)
            if outcome is not None:
                _cancel_timers(instance)                 # finished: nothing is left to wait for
            else:
                _cancel_timers(instance, old_state)
                _start_timers(instance, body, instance.state, now)
            record(actor_ref=actor_ref, action="workflow.transition",
                   object_type="workflow_instance", object_ref=instance.public_id,
                   outcome=AuditOutcome.ALLOWED)
            emit(event_type="workflow.instance.transitioned.v1",
                 aggregate_type="workflow_instance", aggregate_ref=instance.public_id,
                 payload={"instance_ref": instance.public_id, "transition": transition,
                          "from_state": old_state, "to_state": instance.state,
                          "status": instance.status})
        return instance_dict(instance)

    def fire_due_timers(self, *, clock: Clock | None = None, limit: int = 100) -> int:
        """Raise an exception for each timer that has run out. Returns how many were raised. A
        timer whose journey ended or moved on is quietly cancelled instead (stale: a no-op)."""
        now = utc_now(clock)
        raised = 0
        with transaction.atomic():
            due = list(
                WorkflowTimer.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("instance")
                .filter(status=TimerStatus.PENDING, deadline_at__lte=now, **_scope())
                .order_by("deadline_at")[:limit])
            for timer in due:
                instance = timer.instance
                if instance.status != InstanceStatus.RUNNING or instance.state != timer.state_name:
                    timer.status = TimerStatus.CANCELLED
                    timer.save(update_fields=["status", "updated_at"])
                    continue
                timer.status = TimerStatus.FIRED
                timer.fired_at = now
                timer.save(update_fields=["status", "fired_at", "updated_at"])
                WorkflowException.objects.create(
                    instance=instance, kind="timer_expired", detail=timer.name, raised_at=now,
                    **_scope())
                record(actor_ref="system", action="workflow.timer_fired",
                       object_type="workflow_instance", object_ref=instance.public_id,
                       outcome=AuditOutcome.ALLOWED)
                emit(event_type="workflow.timer.fired.v1", aggregate_type="workflow_instance",
                     aggregate_ref=instance.public_id,
                     payload={"instance_ref": instance.public_id, "timer": timer.name,
                              "state": timer.state_name},
                     dedupe_key=f"timer:{timer.public_id}")
                raised += 1
        return raised

    def get_instance(self, instance_ref: str) -> dict[str, Any]:
        row = (WorkflowInstance.objects.select_related("definition")
               .filter(public_id=instance_ref, **_scope()).first())
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        return instance_dict(row)


engine: WorkflowEngine = DbWorkflowEngine()


# --------------------------------------------------------------------------- reading


def instance_history(instance_ref: str, organization_ref: str = "") -> dict[str, Any]:
    """The steps of one journey, oldest first, and whether the chain still checks out."""
    scope = _scope()
    found = WorkflowInstance.objects.filter(public_id=instance_ref, **scope)
    if organization_ref:
        found = found.filter(organization_ref=organization_ref)
    instance = found.first()
    if instance is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    rows = list(WorkflowTransition.objects.select_related("instance")
                .filter(instance=instance, **scope).order_by("seq"))
    ok, bad = verify_chain(rows, _parts)
    return {
        "instance_ref": instance.public_id, "chain_ok": ok, "bad_seq": bad,
        "steps": [
            {"seq": r.seq, "transition": r.transition, "from_state": r.from_state,
             "to_state": r.to_state, "actor_ref": r.actor_ref, "actor_kind": r.actor_kind,
             "evidence": r.evidence, "occurred_at": r.occurred_at.isoformat()}
            for r in rows],
    }


def list_exceptions(
    *, open_only: bool = True, organization_ref: str = "", limit: int = 200
) -> list[dict[str, Any]]:
    rows = WorkflowException.objects.select_related(
        "instance", "instance__definition").filter(**_scope())
    if open_only:
        rows = rows.filter(status=ExceptionStatus.OPEN)
    if organization_ref:
        rows = rows.filter(instance__organization_ref=organization_ref)
    return [
        {"public_id": r.public_id, "instance_ref": r.instance.public_id, "kind": r.kind,
         "detail": r.detail, "status": r.status, "raised_at": r.raised_at.isoformat(),
         "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
         "resolved_by_ref": r.resolved_by_ref, "subject_ref": r.instance.subject_ref,
         "journey": f"{r.instance.definition.key} v{r.instance.definition.version}",
         "journey_state": r.instance.state}
        for r in rows.order_by("raised_at")[:limit]]


def running_instance_for(subject_ref: str, definition_key: str) -> dict[str, Any] | None:
    """The journey of this kind that is running for this subject, if there is one."""
    row = (WorkflowInstance.objects.select_related("definition")
           .filter(subject_ref=subject_ref, definition__key=definition_key,
                   status=InstanceStatus.RUNNING, **_scope()).first())
    return instance_dict(row) if row else None


def instances_for(subject_refs: list[str], definition_key: str) -> dict[str, dict[str, Any]]:
    """Each subject's newest journey of this kind (any state), by subject."""
    rows = (WorkflowInstance.objects.select_related("definition")
            .filter(subject_ref__in=subject_refs, definition__key=definition_key, **_scope())
            .order_by("created_at"))
    return {r.subject_ref: instance_dict(r) for r in rows}      # a later one replaces an earlier


def ensure_bundled_definitions() -> None:
    """Publish the bundled definitions if this cell has none yet (cheap and safe to repeat)."""
    load_bundled_definitions()


def list_instances(
    *, organization_ref: str = "", status: str = "", limit: int = 100
) -> list[dict[str, Any]]:
    """Journeys of this customer, newest first (one clinic only, when `organization_ref` is
    given)."""
    rows = WorkflowInstance.objects.select_related("definition").filter(**_scope())
    if organization_ref:
        rows = rows.filter(organization_ref=organization_ref)
    if status:
        rows = rows.filter(status=status)
    return [instance_dict(i) | {"created_at": i.created_at.isoformat()}
            for i in rows.order_by("-created_at")[:limit]]


def resolve_exception(
    exception_ref: str, *, actor_ref: str, organization_ref: str = "", clock: Clock | None = None
) -> None:
    """A person has looked at the exception and dealt with it. Resolving twice changes nothing."""
    with transaction.atomic():
        found = WorkflowException.objects.select_for_update(of=("self",)).filter(
            public_id=exception_ref, **_scope())
        if organization_ref:
            found = found.filter(instance__organization_ref=organization_ref)
        row = found.first()
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if row.status == ExceptionStatus.RESOLVED:
            return
        row.status = ExceptionStatus.RESOLVED
        row.resolved_at = utc_now(clock)
        row.resolved_by_ref = actor_ref
        row.save(update_fields=["status", "resolved_at", "resolved_by_ref", "updated_at"])
        record(actor_ref=actor_ref, action="workflow.exception_resolved",
               object_type="workflow_exception", object_ref=row.public_id,
               outcome=AuditOutcome.ALLOWED)
