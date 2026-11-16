"""Running a workflow: start, move forward on evidence, hold, cancel, close, time out (GHE-12).

The rules that matter, each with the spec section it comes from:

* A step moves forward only to the NEXT state of the definition, and only with evidence of the right
  kind (section 04). Transport receipts, read receipts, elapsed time, payment or a silent
  partner are refused by name: they are the "disallowed shortcuts".
* Every move names the version it expects to find (compare-and-swap, WF-04). A stale version is a
  conflict to re-read, never retried blindly. A repeated idempotency key returns the first answer.
* Only the instance's accountable owner (or a clinic administrator) may move it.
* Health (clear / at risk / exception / reconciling) is a separate overlay, never a shortcut
  through the states (WF-03).
* Closing needs the closure predicate (WF-07): at the last step before "closed", no exception or
  reconciling overlay, no escalation still waiting for a person. It stores a digest of the whole
  history. "Closed" does not mean recovered or treated.
* A timer running out marks the instance at risk and opens an escalation for a named person. Only a
  person recording that a human was reached closes it; nothing automatic ever does (WF-08).
* Every step is in one chain, written in the same transaction as the change it describes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q

from foundation.cell_context import require_context, tenant_transaction
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .models import (
    DefinitionStatus,
    Escalation,
    EscalationStatus,
    Overlay,
    State,
    TimerStatus,
    Transition,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowTimer,
)

WRITE = "write:workflow"
READ = "read:workflow"
STAFF_ROLES = frozenset({"R04", "R13"})
ADMIN_ROLE = "R13"
TERMINAL = frozenset({"closed", "cancelled"})
# A review or a handoff is the responsible person's own act: an administrator may not enter it on
# their behalf (GHE-12 WF-T53). To act, the administrator must first make themselves accountable,
# which is a visible, audited step.
OWNER_ONLY_TARGETS = frozenset({"clinical_reviewed", "handoff_acknowledged"})

# The evidence a move INTO each state needs (GHE-12 section 04).
EDGE_EVIDENCE = {
    "validated": "validation",
    "accepted": "acceptance_attestation",
    "in_progress": "work_start",
    "output_available": "output_reference",
    "clinical_reviewed": "review_attestation",
    "handoff_acknowledged": "handoff_acceptance",
    "closed": "closure_evidence",
    "cancelled": "cancellation_authorization",
    "blocked": "block_reason",
}
EXTRA_KINDS = {"request_origin", "block_resolution", "exception_resolution"}
ALLOWED_KINDS = frozenset(EDGE_EVIDENCE.values()) | EXTRA_KINDS
# "Disallowed shortcuts": things that happen but prove none of the facts above.
REFUSED_KINDS = frozenset({
    "transport_ack", "email_sent", "tcp_ok", "read_receipt", "viewed", "download",
    "elapsed_time", "payment_received", "partner_silent", "notification_delivered"})
MAX_EVIDENCE = 8
_REF = re.compile(r"^[A-Za-z0-9._:-]{4,64}$")
_SUBJECT = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
_IDEM = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _conflict() -> ZoikoError:
    return ZoikoError(ErrorCode.CONFLICT_VERSION)


def _clean_note(value: str, low: int = 0) -> str:
    text = " ".join((value or "").split())
    if len(text) > 300 or len(text) < low or any(ord(ch) < 32 for ch in text):
        raise _bad("note")
    return text


def check_evidence(items: Any, required: str | None) -> list[dict[str, str]]:
    """Typed, small, and never a transport receipt. `required` must be present."""
    if items is None:
        items = []
    if not isinstance(items, list) or len(items) > MAX_EVIDENCE:
        raise _bad("evidence")
    clean: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"kind", "ref"}:
            raise _bad("evidence")
        kind, ref = item["kind"], item["ref"]
        if (not isinstance(kind, str) or kind in REFUSED_KINDS or kind not in ALLOWED_KINDS
                or not isinstance(ref, str) or not _REF.match(ref)):
            raise _bad("evidence")
        clean.append({"kind": kind, "ref": ref})
    if required and not any(e["kind"] == required for e in clean):
        raise _bad("evidence")
    return clean


# ------------------------------------------------------------------ the chain of steps
def _parts(row: Transition) -> list[Any]:
    return [row.instance.public_id, row.kind, row.from_state, row.to_state, row.overlay_after,
            row.actor_ref, json.dumps(row.evidence, sort_keys=True), row.note,
            row.definition_version, row.occurred_at.isoformat()]


def _append(
    inst: WorkflowInstance, kind: str, from_state: str, *, actor: str, role: str = "",
    purpose: str = "", evidence: list[dict[str, str]] | None = None, note: str = "",
    idem: str = "", decision_ref: str = "", now: dt.datetime,
) -> Transition:
    last = Transition.objects.filter(instance=inst, **_scope()).order_by("-seq").first()
    seq = last.seq + 1 if last else 1
    prev = last.record_digest if last else GENESIS
    row = Transition(
        instance=inst, seq=seq, kind=kind, from_state=from_state, to_state=inst.state,
        overlay_after=inst.overlay, actor_ref=actor, role_code=role, purpose=purpose[:32],
        evidence=evidence or [], note=note, idempotency_key=idem,
        definition_version=inst.definition_version, policy_digest=policy.POLICY_DIGEST,
        decision_ref=decision_ref, occurred_at=now, prev_digest=prev, record_digest="",
        **_scope())
    row.record_digest = chain_digest(prev, seq, *_parts(row))
    try:
        with transaction.atomic():
            row.save()
    except IntegrityError:
        raise _conflict() from None
    # In the same transaction as the step, so a step that commits always has its event and one that
    # rolls back never does (GHE-12 WF-T43, DEV-153). References only.
    emit(event_type="workflow.changed.v1", aggregate_type="workflow", aggregate_ref=inst.public_id,
         payload={"instance_ref": inst.public_id, "kind": kind, "state": inst.state,
                  "overlay": inst.overlay},
         ordering_key=inst.public_id)
    return row


def replay(inst: WorkflowInstance) -> dict[str, Any]:
    """Rebuild what the instance should look like from its history alone and compare (WF-14).
    Every step changes the version by exactly one, so the version must equal the number of steps."""
    rows = list(Transition.objects.select_related("instance").filter(
        instance=inst, **_scope()).order_by("seq"))
    intact, bad = verify_chain(rows, _parts)
    problems = [] if intact else ["history"]
    if rows:
        last = rows[-1]
        if last.to_state != inst.state:
            problems.append("state")
        if last.overlay_after != inst.overlay:
            problems.append("overlay")
        owners = [r.note for r in rows if r.kind == "owner"]
        if owners and owners[-1] != inst.owner_ref:
            problems.append("owner")
        if len(rows) != inst.entity_version:
            problems.append("version")
    else:
        problems.append("history")
    return {"ok": not problems, "problems": problems, "first_bad_step": bad, "steps": len(rows)}


def _hold_if_broken(inst: WorkflowInstance) -> None:
    """A workflow whose history no longer agrees with itself is frozen until a person looks: it
    must not move on top of evidence nobody can trust (WF-T32, WF-T33)."""
    if not replay(inst)["ok"]:
        raise ZoikoError(ErrorCode.HOLD_ACTIVE)


def history(instance_ref: str, organization_ref: str) -> dict[str, Any]:
    inst = _find(instance_ref, organization_ref, lock=False)
    rows = list(Transition.objects.select_related("instance").filter(
        instance=inst, **_scope()).order_by("seq"))
    ok, bad = verify_chain(rows, _parts)
    return {
        "instance": instance_dict(inst), "chain_ok": ok, "bad_seq": bad,
        "replay": replay(inst),
        "steps": [{
            "seq": r.seq, "kind": r.kind, "from_state": r.from_state, "to_state": r.to_state,
            "overlay": r.overlay_after, "actor": r.actor_ref, "role": r.role_code,
            "evidence": r.evidence, "note": r.note, "occurred_at": r.occurred_at.isoformat(),
            "definition_version": r.definition_version, "decision": r.decision_ref}
            for r in rows],
    }


# ------------------------------------------------------------------ looking things up
def _find(ref: str, organization_ref: str, *, lock: bool = True) -> WorkflowInstance:
    query = WorkflowInstance.objects.select_related("definition")
    if lock:
        query = query.select_for_update(of=("self",))
    found = query.filter(public_id=ref, organization_ref=organization_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def instance_dict(inst: WorkflowInstance) -> dict[str, Any]:
    timers = WorkflowTimer.objects.filter(
        instance=inst, status=TimerStatus.PENDING, **_scope()).order_by("due_at")
    waiting = Escalation.objects.filter(
        instance=inst, status=EscalationStatus.OPEN, **_scope()).count()
    return {
        "public_id": inst.public_id, "definition": inst.definition_key,
        "definition_version": inst.definition_version, "journey": inst.journey,
        "subject_ref": inst.subject_ref, "organization_ref": inst.organization_ref,
        "state": inst.state, "overlay": inst.overlay, "owner": inst.owner_ref,
        "version": inst.entity_version, "created_at": inst.created_at.isoformat(),
        "closed_at": inst.closed_at.isoformat() if inst.closed_at else None,
        "closure_digest": inst.closure_digest, "blocked_owner": inst.blocked_owner_ref,
        "open_escalations": waiting,
        "timers": [{"kind": t.kind, "due_at": t.due_at.isoformat(), "watches": t.watches_state}
                   for t in timers],
        "path": list(inst.definition.path),
    }


def _receipt(decision: policy.Decision, actor: str, organization_ref: str) -> None:
    policy.check_receipt(decision, actor_public_id=actor, operation=WRITE,
                         organization_public_id=organization_ref)
    if decision.role_code not in STAFF_ROLES:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


def _may_commit(inst: WorkflowInstance, actor: str, decision: policy.Decision) -> None:
    """Only the accountable owner (or a clinic administrator) may move an instance (WF-04)."""
    if actor != inst.owner_ref and decision.role_code != ADMIN_ROLE:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


def _check_owner(owner_ref: str) -> None:
    if owner_ref not in trust.actor_labels():
        raise _bad("owner")


# ------------------------------------------------------------------ timers
def _stale_timers(inst: WorkflowInstance, only_state: str | None = None) -> None:
    pending = WorkflowTimer.objects.filter(
        instance=inst, status=TimerStatus.PENDING, **_scope())
    if only_state:
        pending = pending.filter(watches_state=only_state)
    pending.update(status=TimerStatus.STALE)


def _start_timers(inst: WorkflowInstance, now: dt.datetime) -> None:
    for item in inst.definition.timer_policies:
        if item["state"] == inst.state:
            WorkflowTimer.objects.create(
                instance=inst, kind=item["kind"], watches_state=inst.state,
                due_at=now + dt.timedelta(minutes=item["after_minutes"]),
                owner_ref=inst.owner_ref, **_scope())


# ------------------------------------------------------------------ starting
def start(
    *, definition_key: str, subject_ref: str, organization_ref: str, owner_ref: str,
    evidence: Any, purpose: str, actor_public_id: str, decision: policy.Decision,
    idempotency_key: str = "", enabled_capabilities: frozenset[str] | None = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """NONE -> REQUESTED. Needs a staff actor, the request origin as evidence, a named owner.

    With an idempotency key, asking again gives the first answer (and `replayed` true) without a
    second workflow; the same key with a different request is a conflict."""
    _receipt(decision, actor_public_id, organization_ref)
    if not _SUBJECT.match(subject_ref or ""):
        raise _bad("subject_ref")
    if idempotency_key and not _IDEM.match(idempotency_key):
        raise _bad("idempotency_key")
    clean = check_evidence(evidence, "request_origin")
    _check_owner(owner_ref)
    now = utc_now(clock)
    digest = hashlib.sha256(json.dumps(
        [definition_key, subject_ref, organization_ref, owner_ref]).encode()).hexdigest()
    with transaction.atomic():
        if idempotency_key:
            earlier = WorkflowInstance.objects.select_related("definition").filter(
                start_key=idempotency_key, **_scope()).first()
            if earlier is not None:
                if earlier.start_digest != digest or earlier.organization_ref != organization_ref:
                    raise _conflict()
                return {**instance_dict(earlier), "replayed": True}
        definition = (WorkflowDefinition.objects.filter(
            key=definition_key, status=DefinitionStatus.PUBLISHED, **_scope())
            .order_by("-version").first())
        if definition is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        # GHE-12 WF-T01 (DEV-156): a definition that needs country-pack capabilities starts only
        # where a signed, current pack switches every one of them on. No answer means no.
        if set(definition.required_capabilities) - (enabled_capabilities or frozenset()):
            raise ZoikoError(ErrorCode.JURISDICTION_INACTIVE)
        inst = WorkflowInstance(
            definition=definition, definition_key=definition.key,
            definition_version=definition.version, journey=definition.journey,
            organization_ref=organization_ref, subject_ref=subject_ref, owner_ref=owner_ref,
            state=State.REQUESTED, overlay=Overlay.CLEAR, start_key=idempotency_key,
            start_digest=digest if idempotency_key else "", **_scope())
        try:
            with transaction.atomic():
                inst.save()
        except IntegrityError:
            raise _conflict() from None          # this subject already has one running
        _append(inst, "transition", "", actor=actor_public_id, role=decision.role_code,
                purpose=purpose, evidence=clean, decision_ref=decision.decision_public_id,
                now=now)
        _start_timers(inst, now)
        record(actor_ref=actor_public_id, action="orchestration.requested",
               object_type="workflow_instance", object_ref=inst.public_id,
               outcome=AuditOutcome.ALLOWED)
        return {**instance_dict(inst), "replayed": False}


# ------------------------------------------------------------------ moving
def _seen(inst: WorkflowInstance, idem: str) -> Transition | None:
    if not idem:
        return None
    return Transition.objects.filter(instance=inst, idempotency_key=idem, **_scope()).first()


def _closure_blockers(inst: WorkflowInstance) -> list[str]:
    path = list(inst.definition.path)
    blockers = []
    if inst.state != path[-2]:
        blockers.append("not_at_last_step")
    if inst.overlay in (Overlay.EXCEPTION, Overlay.RECONCILING):
        blockers.append("open_exception")
    if Escalation.objects.filter(instance=inst, status=EscalationStatus.OPEN,
                                 **_scope()).exists():
        blockers.append("open_escalation")
    return blockers


def _closure_digest(inst: WorkflowInstance) -> str:
    last = Transition.objects.filter(instance=inst, **_scope()).order_by("-seq").first()
    return chain_digest(last.record_digest if last else GENESIS, 0, inst.public_id,
                        inst.definition_key, inst.definition_version, inst.subject_ref)


def advance(
    *, instance_ref: str, organization_ref: str, expected_version: int, to_state: str,
    evidence: Any, idempotency_key: str, purpose: str, note: str, actor_public_id: str,
    decision: policy.Decision, incident_owner_ref: str = "",
    enabled_capabilities: frozenset[str] | None = None, clock: Clock | None = None,
) -> dict[str, Any]:
    """Move to the next state (or cancel, block or close) on typed evidence, once, safely.

    A workflow whose definition needs country-pack capabilities stops moving forward when a signed
    current pack no longer switches them on (suspended, expired, withdrawn): GHE-13 T-62, DEV-160.
    Cancelling and blocking stay possible, so a person can always close it down safely."""
    _receipt(decision, actor_public_id, organization_ref)
    if to_state not in State.values:
        raise _bad("to_state")
    if not _IDEM.match(idempotency_key or ""):
        raise _bad("idempotency_key")
    now = utc_now(clock)
    with transaction.atomic():
        inst = _find(instance_ref, organization_ref)
        earlier = _seen(inst, idempotency_key)
        if earlier is not None:                       # the same request again: the first answer
            if earlier.to_state != to_state:
                raise _conflict()
            return instance_dict(inst)
        _may_commit(inst, actor_public_id, decision)
        _hold_if_broken(inst)
        if (to_state not in ("cancelled", "blocked")
                and set(inst.definition.required_capabilities) - (enabled_capabilities
                                                                  or frozenset())):
            raise ZoikoError(ErrorCode.JURISDICTION_INACTIVE)
        if to_state in OWNER_ONLY_TARGETS and actor_public_id != inst.owner_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)  # not on the responsible person's behalf
        if inst.entity_version != expected_version:
            raise _conflict()                          # re-read; never a blind retry
        if inst.state in TERMINAL or inst.state == State.BLOCKED:
            raise _conflict()
        path = list(inst.definition.path)
        required = EDGE_EVIDENCE.get(to_state)
        clean = check_evidence(evidence, required)
        text = ""
        if to_state in ("cancelled", "blocked"):
            text = _clean_note(note, low=3)
        if to_state == "blocked":
            _check_owner(incident_owner_ref)           # a named incident owner (section 04)
        elif to_state == "cancelled":
            pass
        else:
            nxt = path[path.index(inst.state) + 1]
            if to_state != nxt:
                raise _bad("to_state")                # no shortcut, no skipping a required step
            if to_state == "closed":
                blockers = _closure_blockers(inst)
                if blockers:
                    raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=blockers)
        old = inst.state
        _stale_timers(inst)
        if to_state == "blocked":
            inst.blocked_from, inst.blocked_owner_ref = old, incident_owner_ref
        inst.state = State(to_state)
        if to_state == "closed":
            inst.closed_at = now
            inst.closure_digest = _closure_digest(inst)
        inst.entity_version += 1
        inst.save()
        _append(inst, "transition", old, actor=actor_public_id, role=decision.role_code,
                purpose=purpose, evidence=clean, note=text,
                idem=idempotency_key, decision_ref=decision.decision_public_id, now=now)
        if to_state not in TERMINAL and to_state != "blocked":
            _start_timers(inst, now)
        record(actor_ref=actor_public_id, action=f"orchestration.{to_state}",
               object_type="workflow_instance", object_ref=inst.public_id,
               outcome=AuditOutcome.ALLOWED)
        return instance_dict(inst)


def resume(
    *, instance_ref: str, organization_ref: str, expected_version: int, evidence: Any,
    note: str, actor_public_id: str, decision: policy.Decision, clock: Clock | None = None,
) -> dict[str, Any]:
    """Lift a block, back to the state it was in (the named incident owner or an administrator)."""
    _receipt(decision, actor_public_id, organization_ref)
    now = utc_now(clock)
    with transaction.atomic():
        inst = _find(instance_ref, organization_ref)
        if inst.state != State.BLOCKED:
            raise _conflict()
        if actor_public_id != inst.blocked_owner_ref and decision.role_code != ADMIN_ROLE:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        _hold_if_broken(inst)
        if inst.entity_version != expected_version:
            raise _conflict()
        clean = check_evidence(evidence, "block_resolution")
        text = _clean_note(note, low=3)
        inst.state = State(inst.blocked_from)
        inst.blocked_from = ""
        inst.entity_version += 1
        inst.save()
        _append(inst, "transition", "blocked", actor=actor_public_id, role=decision.role_code,
                evidence=clean, note=text, decision_ref=decision.decision_public_id, now=now)
        _start_timers(inst, now)
        record(actor_ref=actor_public_id, action="orchestration.resumed",
               object_type="workflow_instance", object_ref=inst.public_id,
               outcome=AuditOutcome.ALLOWED)
        return instance_dict(inst)


def set_overlay(
    *, instance_ref: str, organization_ref: str, expected_version: int, overlay: str, note: str,
    actor_public_id: str, decision: policy.Decision, evidence: Any = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Operational health. It never changes the state. Clearing it needs a reason and no
    escalation still waiting for a person; clearing an exception or a reconciliation also needs
    evidence of its resolution (GHE-12 WF-T54, DEV-147), not just somebody's word."""
    _receipt(decision, actor_public_id, organization_ref)
    if overlay not in Overlay.values:
        raise _bad("overlay")
    now = utc_now(clock)
    with transaction.atomic():
        inst = _find(instance_ref, organization_ref)
        _may_commit(inst, actor_public_id, decision)
        _hold_if_broken(inst)
        if inst.entity_version != expected_version:
            raise _conflict()
        if inst.state in TERMINAL:
            raise _conflict()
        if inst.overlay == overlay:
            return instance_dict(inst)
        text = _clean_note(note, low=3)
        needs_proof = overlay == Overlay.CLEAR and inst.overlay in (
            Overlay.EXCEPTION, Overlay.RECONCILING)
        clean = check_evidence(evidence, "exception_resolution" if needs_proof else None)
        if overlay == Overlay.CLEAR and Escalation.objects.filter(
                instance=inst, status=EscalationStatus.OPEN, **_scope()).exists():
            raise _conflict()                          # a person has not been reached yet
        inst.overlay = Overlay(overlay)
        inst.entity_version += 1
        inst.save()
        _append(inst, "overlay", inst.state, actor=actor_public_id, role=decision.role_code,
                evidence=clean, note=text, decision_ref=decision.decision_public_id, now=now)
        record(actor_ref=actor_public_id, action=f"orchestration.overlay_{overlay}",
               object_type="workflow_instance", object_ref=inst.public_id,
               outcome=AuditOutcome.ALLOWED)
        return instance_dict(inst)


def reassign(
    *, instance_ref: str, organization_ref: str, expected_version: int, new_owner_ref: str,
    actor_public_id: str, decision: policy.Decision, clock: Clock | None = None,
) -> dict[str, Any]:
    """A clinic administrator names a different accountable person."""
    _receipt(decision, actor_public_id, organization_ref)
    if decision.role_code != ADMIN_ROLE:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _check_owner(new_owner_ref)
    now = utc_now(clock)
    with transaction.atomic():
        inst = _find(instance_ref, organization_ref)
        _hold_if_broken(inst)
        if inst.entity_version != expected_version:
            raise _conflict()
        if inst.state in TERMINAL:
            raise _conflict()
        if inst.owner_ref == new_owner_ref:
            return instance_dict(inst)
        inst.owner_ref = new_owner_ref
        inst.entity_version += 1
        inst.save()
        WorkflowTimer.objects.filter(instance=inst, status=TimerStatus.PENDING,
                                     **_scope()).update(owner_ref=new_owner_ref)
        Escalation.objects.filter(instance=inst, status=EscalationStatus.OPEN,
                                  **_scope()).update(owner_ref=new_owner_ref)
        _append(inst, "owner", inst.state, actor=actor_public_id, role=decision.role_code,
                note=new_owner_ref, decision_ref=decision.decision_public_id, now=now)
        record(actor_ref=actor_public_id, action="orchestration.reassigned",
               object_type="workflow_instance", object_ref=inst.public_id,
               outcome=AuditOutcome.ALLOWED)
        return instance_dict(inst)


# ------------------------------------------------------------------ time running out
def fire_due_timers(*, clock: Clock | None = None, limit: int = 100) -> dict[str, int]:
    """In the CURRENT customer: check each due timer still matters, then mark the instance at risk
    and open an escalation for the named owner. Nothing here says a person was told or reached."""
    now = utc_now(clock)
    counts = {"fired": 0, "stale": 0}
    with transaction.atomic():
        due = list(WorkflowTimer.objects.select_for_update(skip_locked=True, of=("self",))
                   .filter(status=TimerStatus.PENDING, due_at__lte=now, **_scope())
                   .order_by("due_at")[:limit])
        for timer in due:
            inst = (WorkflowInstance.objects.select_for_update(of=("self",))
                    .select_related("definition").get(id=timer.instance_id))
            if inst.state != timer.watches_state or inst.state in TERMINAL:
                timer.status = TimerStatus.STALE        # the step it watched is over
                timer.save(update_fields=["status", "updated_at"])
                counts["stale"] += 1
                continue
            timer.status, timer.fired_at = TimerStatus.FIRED, now
            timer.save(update_fields=["status", "fired_at", "updated_at"])
            if inst.overlay == Overlay.CLEAR:
                inst.overlay = Overlay.AT_RISK
                inst.entity_version += 1
                inst.save()
                _append(inst, "timer", inst.state, actor="system", note=f"timer:{timer.kind}",
                        now=now)
            Escalation.objects.create(
                instance=inst, timer=timer, owner_ref=timer.owner_ref or inst.owner_ref,
                **_scope())
            counts["fired"] += 1
    return counts


def fire_all_tenants(limit: int = 100) -> int:
    """For the worker: every active customer in turn. Returns how many timers fired."""
    total = 0
    for tenant in trust.list_tenants():
        if tenant["status"] != "active":
            continue
        with tenant_transaction(trust.context_for(tenant["public_id"])):
            total += fire_due_timers(limit=limit)["fired"]
    return total


def record_escalation(
    *, escalation_ref: str, organization_ref: str, reached: bool, note: str,
    fallback_owner_ref: str, actor_public_id: str, decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """A PERSON says whether a human was reached and took ownership. Only "reached" closes it; an
    unsuccessful try is counted and may hand the escalation to a fallback person (WF-08)."""
    _receipt(decision, actor_public_id, organization_ref)
    _clean_note(note, low=3)                      # a person must say who was reached, and how
    now = utc_now(clock)
    with transaction.atomic():
        esc = (Escalation.objects.select_for_update(of=("self",)).select_related("instance")
               .filter(public_id=escalation_ref, instance__organization_ref=organization_ref,
                       **_scope()).first())
        if esc is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if actor_public_id != esc.owner_ref and decision.role_code != ADMIN_ROLE:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if esc.status != EscalationStatus.OPEN:
            return escalation_dict(esc)                 # already settled
        if reached:
            esc.status, esc.closed_by_ref, esc.closed_at = (
                EscalationStatus.REACHED, actor_public_id, now)
        else:
            esc.attempts += 1
            if fallback_owner_ref:
                _check_owner(fallback_owner_ref)
                esc.owner_ref = fallback_owner_ref
        esc.entity_version += 1
        esc.save()
        record(actor_ref=actor_public_id,
               action="orchestration.escalation_reached" if reached
               else "orchestration.escalation_not_reached",
               object_type="escalation", object_ref=esc.public_id, outcome=AuditOutcome.ALLOWED)
        return escalation_dict(esc)


def escalation_dict(e: Escalation) -> dict[str, Any]:
    return {
        "public_id": e.public_id, "instance": e.instance.public_id, "owner": e.owner_ref,
        "status": e.status, "attempts": e.attempts, "created_at": e.created_at.isoformat(),
        "closed_at": e.closed_at.isoformat() if e.closed_at else None,
    }


# ------------------------------------------------------------------ lists
def list_instances(organization_ref: str, *, workbench: bool = False, state: str | None = None,
                   limit: int = 100) -> list[dict[str, Any]]:
    rows = WorkflowInstance.objects.select_related("definition").filter(
        organization_ref=organization_ref, **_scope())
    if state:
        if state not in State.values:
            raise _bad("state")
        rows = rows.filter(state=state)
    gone = trust.inactive_actors()
    if workbench:        # the exception workbench: anything that needs a person (WF-15)
        waiting = Escalation.objects.filter(
            status=EscalationStatus.OPEN, **_scope()).values_list("instance_id", flat=True)
        rows = rows.exclude(state__in=list(TERMINAL)).filter(
            ~Q(overlay=Overlay.CLEAR) | Q(state=State.BLOCKED) | Q(id__in=waiting)
            | Q(owner_ref__in=gone))
    out = [instance_dict(r) for r in rows.order_by("-created_at")[: min(limit, 200)]]
    for item in out:      # WF-T42: a live workflow whose owner can no longer act says so
        item["owner_inactive"] = item["owner"] in gone and item["state"] not in TERMINAL
    return out


def ownerless_count(organization_ref: str) -> int:
    """Live workflows whose accountable owner has been deactivated and so cannot move them (GHE-12
    WF-T42, DEV-154). An administrator can still reassign them."""
    gone = trust.inactive_actors()
    if not gone:
        return 0
    return WorkflowInstance.objects.filter(
        organization_ref=organization_ref, owner_ref__in=gone, **_scope()
    ).exclude(state__in=list(TERMINAL)).count()


def list_escalations(organization_ref: str, open_only: bool = True) -> list[dict[str, Any]]:
    rows = Escalation.objects.select_related("instance").filter(
        instance__organization_ref=organization_ref, **_scope())
    if open_only:
        rows = rows.filter(status=EscalationStatus.OPEN)
    return [escalation_dict(e) for e in rows.order_by("created_at")[:200]]
