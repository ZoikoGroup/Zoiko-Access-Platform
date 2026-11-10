"""Asking a partner to do something, and keeping honest about what became of it.

The life of an operation (architecture sections 5 and 9, GHE-13):

  request_operation()   ONE transaction: the operation row, its first history step, the audit
                        record and the outbox event. Nothing is sent to the partner here.
  send_due()            a worker claims due operations (FOR UPDATE SKIP LOCKED) and marks them
                        `sent` in one short transaction, calls the partner OUTSIDE any transaction,
                        then records the answer in another. A crash in between leaves `sent`, which
                        recover_stuck() turns into `unknown`.
  reconcile_due()       asks the partner what became of operations that were accepted but not
                        confirmed, or whose outcome is unknown.

Rules that hold throughout
- A technical receipt (`accepted`) is never an outcome. Only the partner's own `confirmed` counts,
  and only `confirmed` makes `is_source_confirmed()` true.
- A call that may have reached the partner is never simply sent again. We ask first.
- Failures keep only the exception CLASS, never its message (messages can hold personal data).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context, tenant_transaction
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.errors import ErrorCode, ZoikoError
from foundation.idempotency import request_digest
from foundation.outbox import backoff_seconds, emit
from foundation.payloads import PayloadError, check_reference_payload
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from . import adapters
from .adapters import AdapterTimeout, AdapterUnavailable, OperationView, Outcome, PartnerResult
from .models import (
    ExternalOperation,
    OperationEvent,
    OperationEventType,
    OperationState,
)

WRITE_PARTNER = "write:partner"
STUCK_AFTER = dt.timedelta(minutes=5)       # `sent` for longer than this means a worker died
POLL_BASE_SECONDS = 30.0


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def operation_dict(op: ExternalOperation) -> dict[str, Any]:
    return {
        "public_id": op.public_id, "connector_key": op.connector_key,
        "operation_type": op.operation_type, "subject_ref": op.subject_ref,
        "organization_ref": op.organization_ref, "state": op.state, "attempts": op.attempts,
        "polls": op.polls, "partner_ref": op.partner_ref, "last_code": op.last_code,
        "last_error": op.last_error, "created_at": op.created_at.isoformat(),
        "sent_at": op.sent_at.isoformat() if op.sent_at else None,
        "finished_at": op.finished_at.isoformat() if op.finished_at else None,
        "source_confirmed": is_source_confirmed(op),
    }


def is_source_confirmed(op: ExternalOperation) -> bool:
    """True only when the PARTNER has confirmed the outcome. A receipt, a timeout that "probably
    worked" or an operator's guess never counts: the other modules must ask this, not look at the
    state themselves."""
    return op.state == OperationState.CONFIRMED


def _view(op: ExternalOperation) -> OperationView:
    return OperationView(
        operation_ref=op.public_id, operation_type=op.operation_type, subject_ref=op.subject_ref,
        idempotency_key=op.idempotency_key, partner_ref=op.partner_ref, request=dict(op.request))


def _parts(row: OperationEvent) -> list[Any]:
    return [row.operation.public_id, row.event_type, row.state_after, row.actor_ref, row.code,
            row.occurred_at.isoformat()]


def _log(op: ExternalOperation, event_type: str, *, actor_ref: str = "system", code: str = "",
         now: dt.datetime) -> None:
    last = OperationEvent.objects.filter(operation=op, **_scope()).order_by("-seq").first()
    seq = last.seq + 1 if last else 1
    prev = last.record_digest if last else GENESIS
    digest = chain_digest(prev, seq, op.public_id, event_type, op.state, actor_ref, code,
                          now.isoformat())
    try:
        with transaction.atomic():
            OperationEvent.objects.create(
                operation=op, seq=seq, event_type=event_type, state_after=op.state,
                actor_ref=actor_ref, code=code, occurred_at=now, prev_digest=prev,
                record_digest=digest, **_scope())
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None


def _announce(op: ExternalOperation, kind: str) -> None:
    """Tell the rest of the product, by event. Payloads are references and the state only."""
    emit(event_type=f"connector.operation.{kind}.v1", aggregate_type="external_operation",
         aggregate_ref=op.public_id,
         payload={"operation_ref": op.public_id, "operation_type": op.operation_type,
                  "subject_ref": op.subject_ref, "state": op.state},
         dedupe_key=f"op:{op.public_id}:{kind}")


def _settle(op: ExternalOperation, result: PartnerResult, now: dt.datetime) -> None:
    """Move an operation to where the partner's answer puts it. Call inside a transaction."""
    if result.partner_ref:
        op.partner_ref = result.partner_ref[:128]
    op.last_code = result.code[:64]
    if result.outcome is Outcome.CONFIRMED:
        op.state, op.finished_at = OperationState.CONFIRMED, now
        _log(op, OperationEventType.CONFIRMED, now=now)
        _announce(op, "confirmed")
    elif result.outcome is Outcome.REJECTED:
        op.state, op.finished_at = OperationState.REJECTED, now
        _log(op, OperationEventType.REJECTED, code=result.code[:64], now=now)
        _announce(op, "rejected")
    elif op.state != OperationState.ACCEPTED:              # a receipt: wait for the real answer
        op.state = OperationState.ACCEPTED
        _log(op, OperationEventType.ACCEPTED, now=now)
        _announce(op, "accepted")
    if op.state == OperationState.ACCEPTED:
        op.next_attempt_at = now + dt.timedelta(seconds=POLL_BASE_SECONDS)


# --------------------------------------------------------------------------- asking


def request_operation(
    *, connector_key: str, operation_type: str, subject_ref: str, organization_ref: str,
    idempotency_key: str, request: dict[str, Any], requested_by_ref: str,
    decision: policy.Decision, max_attempts: int = 5, clock: Clock | None = None,
) -> dict[str, Any]:
    """Queue an operation for a partner. The caller must hold a policy receipt for
    `write:partner` at this organization. Asking again with the same key and the same request
    returns the same operation; the same key with a different request is refused."""
    policy.check_receipt(
        decision, actor_public_id=requested_by_ref, operation=WRITE_PARTNER,
        organization_public_id=organization_ref)
    if adapters.get(connector_key) is None:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["connector"])
    if not 1 <= max_attempts <= 10:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["max_attempts"])
    if not 16 <= len(idempotency_key) <= 128:                 # GHE-13: 16 to 128 characters
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["idempotency_key"])
    try:
        body = check_reference_payload(request)
    except PayloadError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["request"]) from None
    digest = request_digest([connector_key, operation_type, subject_ref, body])
    now = utc_now(clock)
    try:
        with transaction.atomic():
            op = ExternalOperation.objects.create(
                connector_key=connector_key, operation_type=operation_type,
                subject_ref=subject_ref, organization_ref=organization_ref,
                idempotency_key=idempotency_key, request=body, request_digest=digest,
                requested_by_ref=requested_by_ref, max_attempts=max_attempts,
                next_attempt_at=now, **_scope())
            _log(op, OperationEventType.REQUESTED, actor_ref=requested_by_ref, now=now)
            record(actor_ref=requested_by_ref, action="connector.request",
                   object_type="external_operation", object_ref=op.public_id,
                   outcome=AuditOutcome.ALLOWED)
            return operation_dict(op)
    except IntegrityError:
        existing = ExternalOperation.objects.filter(
            connector_key=connector_key, idempotency_key=idempotency_key, **_scope()).first()
        if existing is None or existing.request_digest != digest:
            raise ZoikoError(ErrorCode.DUPLICATE_COMMAND) from None
        return operation_dict(existing)                       # the same request: same operation


# --------------------------------------------------------------------------- sending


def _claim_due(now: dt.datetime, limit: int) -> list[ExternalOperation]:
    """Take operations that are due and mark them `sent`, in one short transaction. Several
    workers may run at once: a locked row is skipped, never taken twice."""
    with tenant_transaction():
        due = list(
            ExternalOperation.objects.select_for_update(skip_locked=True)
            .filter(state=OperationState.PENDING, next_attempt_at__lte=now, **_scope())
            .order_by("next_attempt_at")[:limit])
        for op in due:
            op.state = OperationState.SENT
            op.attempts += 1
            op.sent_at = now
            op.save(update_fields=["state", "attempts", "sent_at", "updated_at"])
            _log(op, OperationEventType.SENT, now=now)
    return due


def _record_outcome(op_ref: str, call: Any, now: dt.datetime) -> None:
    """Apply what happened on the call. `call` is a PartnerResult or the exception that came
    back. Runs in its own transaction, after the partner has been called."""
    with tenant_transaction():
        op = ExternalOperation.objects.select_for_update().get(public_id=op_ref, **_scope())
        if op.state != OperationState.SENT:
            return                                              # someone else already settled it
        if isinstance(call, PartnerResult):
            _settle(op, call, now)
        elif isinstance(call, AdapterTimeout):
            # It may have arrived. Do NOT send it again: ask the partner (reconciliation).
            op.state, op.last_error = OperationState.UNKNOWN, "AdapterTimeout"
            op.next_attempt_at = now + dt.timedelta(seconds=POLL_BASE_SECONDS)
            _log(op, OperationEventType.UNKNOWN, code="timeout", now=now)
            _announce(op, "unknown")
        else:                                                   # it certainly did not arrive
            op.last_error = type(call).__name__[:64]
            if op.attempts >= op.max_attempts:
                op.state = OperationState.DEAD
                _log(op, OperationEventType.DEAD, code=op.last_error, now=now)
                _announce(op, "dead")
            else:
                op.state = OperationState.PENDING
                op.next_attempt_at = now + dt.timedelta(seconds=backoff_seconds(op.attempts))
                _log(op, OperationEventType.RETRY, code=op.last_error, now=now)
        op.save()


def send_due(*, clock: Clock | None = None, limit: int = 20) -> int:
    """One worker pass over this tenant's due operations. Returns how many were sent."""
    now = utc_now(clock)
    claimed = _claim_due(now, limit)
    for op in claimed:
        adapter = adapters.get(op.connector_key)
        view = _view(op)
        try:
            if adapter is None:
                raise AdapterUnavailable()                      # not enabled here: treat as down
            outcome: Any = adapter.submit(view)                # NOT inside a transaction
        except (AdapterTimeout, AdapterUnavailable) as exc:
            outcome = exc
        except Exception as exc:  # noqa: BLE001 - an adapter bug: the outcome is unknown, ask
            outcome = AdapterTimeout(type(exc).__name__)
        _record_outcome(op.public_id, outcome, utc_now(clock))
    return len(claimed)


def recover_stuck(*, clock: Clock | None = None) -> int:
    """An operation left `sent` for too long means a worker died mid-call. It may or may not have
    reached the partner, so it becomes `unknown` (and reconciliation asks)."""
    now = utc_now(clock)
    fixed = 0
    with tenant_transaction():
        stuck = list(ExternalOperation.objects.select_for_update(skip_locked=True).filter(
            state=OperationState.SENT, sent_at__lte=now - STUCK_AFTER, **_scope()))
        for op in stuck:
            op.state, op.last_error = OperationState.UNKNOWN, "WorkerLost"
            op.next_attempt_at = now
            op.save(update_fields=["state", "last_error", "next_attempt_at", "updated_at"])
            _log(op, OperationEventType.UNKNOWN, code="worker_lost", now=now)
            _announce(op, "unknown")
            fixed += 1
    return fixed


# --------------------------------------------------------------------------- reconciling


def reconcile_due(*, clock: Clock | None = None, limit: int = 20) -> int:
    """Ask the partner about operations that are `accepted` or `unknown` and due. Returns how many
    were asked."""
    now = utc_now(clock)
    with tenant_transaction():
        due = list(
            ExternalOperation.objects.select_for_update(skip_locked=True)
            .filter(state__in=[OperationState.ACCEPTED, OperationState.UNKNOWN],
                    next_attempt_at__lte=now, **_scope())
            .order_by("next_attempt_at")[:limit])
        for op in due:                                          # claim: push the next ask out
            op.polls += 1
            op.next_attempt_at = now + dt.timedelta(seconds=backoff_seconds(op.polls + 2))
            op.save(update_fields=["polls", "next_attempt_at", "updated_at"])
    for op in due:
        adapter = adapters.get(op.connector_key)
        answer: PartnerResult | None = None
        if adapter is not None:
            try:
                answer = adapter.query(_view(op))
            except Exception:  # noqa: BLE001, S110 - no answer this time: try again later
                answer = None
        _apply_answer(op.public_id, answer, adapter, utc_now(clock))
    return len(due)


def _apply_answer(
    op_ref: str, answer: PartnerResult | None, adapter: adapters.Adapter | None, now: dt.datetime
) -> None:
    with tenant_transaction():
        op = ExternalOperation.objects.select_for_update().get(public_id=op_ref, **_scope())
        if op.state not in (OperationState.ACCEPTED, OperationState.UNKNOWN):
            return
        if answer is not None and answer.outcome in (Outcome.CONFIRMED, Outcome.REJECTED):
            _settle(op, answer, now)
        elif answer is not None and answer.outcome is Outcome.NOT_FOUND and (
                adapter is not None and adapter.idempotent):
            # The partner has no record and honours our key: sending again cannot do it twice.
            op.state = OperationState.PENDING
            op.next_attempt_at = now
            _log(op, OperationEventType.RETRY, code="not_found", now=now)
        elif answer is not None and answer.outcome is Outcome.ACCEPTED and (
                op.state == OperationState.UNKNOWN):
            _settle(op, answer, now)
        if op.state in (OperationState.ACCEPTED, OperationState.UNKNOWN) and (
                op.polls >= op.max_polls):
            op.state = OperationState.DEAD                      # the partner never settled it
            _log(op, OperationEventType.DEAD, code="no_final_answer", now=now)
            _announce(op, "dead")
        op.save()


# --------------------------------------------------------------------------- people step in


def redrive(*, operation_ref: str, actor_ref: str, clock: Clock | None = None) -> dict[str, Any]:
    """A person puts a dead operation back in the queue. The attempt counters start again."""
    now = utc_now(clock)
    with transaction.atomic():
        op = _locked(operation_ref)
        if op.state != OperationState.DEAD:
            raise ZoikoError(ErrorCode.UNSAFE_REPLAY)
        op.state, op.attempts, op.polls = OperationState.PENDING, 0, 0
        op.next_attempt_at = now
        op.save()
        _log(op, OperationEventType.REDRIVEN, actor_ref=actor_ref, now=now)
        record(actor_ref=actor_ref, action="connector.redrive", object_type="external_operation",
               object_ref=op.public_id, outcome=AuditOutcome.ALLOWED)
        return operation_dict(op)


def resolve_by_hand(
    *, operation_ref: str, outcome: str, actor_ref: str, clock: Clock | None = None
) -> dict[str, Any]:
    """A person who has checked with the partner records the real outcome of an operation that
    is `unknown` or `dead`. The history keeps WHO settled it; it is not the partner's word, but
    it is the only way out when the partner cannot be asked."""
    if outcome not in ("confirmed", "rejected"):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["outcome"])
    now = utc_now(clock)
    with transaction.atomic():
        op = _locked(operation_ref)
        if op.state not in (OperationState.UNKNOWN, OperationState.DEAD):
            raise ZoikoError(ErrorCode.UNSAFE_REPLAY)
        op.state = OperationState(outcome)
        op.finished_at = now
        op.save()
        _log(op, OperationEventType.RESOLVED, actor_ref=actor_ref, code=outcome, now=now)
        _announce(op, outcome)
        record(actor_ref=actor_ref, action="connector.resolve", object_type="external_operation",
               object_ref=op.public_id, outcome=AuditOutcome.ALLOWED)
        return operation_dict(op)


def _locked(operation_ref: str) -> ExternalOperation:
    op = ExternalOperation.objects.select_for_update().filter(
        public_id=operation_ref, **_scope()).first()
    if op is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return op


# --------------------------------------------------------------------------- reading


def get_operation(operation_ref: str) -> dict[str, Any]:
    op = ExternalOperation.objects.filter(public_id=operation_ref, **_scope()).first()
    if op is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return operation_dict(op)


def list_operations(
    *, state: str = "", subject_ref: str = "", limit: int = 200
) -> list[dict[str, Any]]:
    rows = ExternalOperation.objects.filter(**_scope())
    if state:
        rows = rows.filter(state=state)
    if subject_ref:
        rows = rows.filter(subject_ref=subject_ref)
    return [operation_dict(o) for o in rows.order_by("-created_at")[:limit]]


def operation_history(operation_ref: str) -> dict[str, Any]:
    """The steps of one operation, oldest first, and whether the chain still checks out."""
    scope = _scope()
    op = ExternalOperation.objects.filter(public_id=operation_ref, **scope).first()
    if op is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    rows = list(OperationEvent.objects.select_related("operation")
                .filter(operation=op, **scope).order_by("seq"))
    ok, bad = verify_chain(rows, _parts)
    return {
        "operation_ref": op.public_id, "state": op.state, "chain_ok": ok, "bad_seq": bad,
        "events": [
            {"seq": r.seq, "event_type": r.event_type, "state_after": r.state_after,
             "actor_ref": r.actor_ref, "code": r.code, "occurred_at": r.occurred_at.isoformat()}
            for r in rows],
    }
