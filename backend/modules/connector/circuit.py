"""A circuit breaker per partner connector (GHE-20 C-08, GHE-13 T-32; DEV-221).

When a partner stops answering, asking it again and again helps nobody. After THRESHOLD failed
calls in a row the circuit OPENS: nothing is sent to that connector and nothing is asked of it, and
its operations simply wait in their own local state (pending, accepted or unknown). Nothing is
lost and nothing is guessed. After the cool-down one trial call is let through (half open): an
answer closes the circuit, another failure opens it again for twice as long (capped).

A partner that says it does not support what we sent (`AdapterUnsupported`: a profile or version
it does not speak) HALTS only that connector until a person resets it, because waiting will not fix
it. Other connectors carry on.

The numbers are the developer's (TEMPORARY): the partner contract should set them.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from foundation.cell_context import require_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import CircuitState, ConnectorCircuit

THRESHOLD = 5
FIRST_COOLDOWN = dt.timedelta(minutes=5)
MAX_COOLDOWN = dt.timedelta(hours=1)


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _row(key: str) -> ConnectorCircuit:
    row = ConnectorCircuit.objects.select_for_update().filter(connector_key=key, **_scope()).first()
    if row is None:
        row = ConnectorCircuit(connector_key=key, **_scope())
        row.save()
    return row


def blocked(key: str, now: dt.datetime) -> tuple[bool, bool]:
    """(blocked, trial): blocked means send and ask nothing; trial means let exactly one call
    through because the cool-down is over."""
    with tenant_transaction():
        row = ConnectorCircuit.objects.filter(connector_key=key, **_scope()).first()
    if row is None or row.state == CircuitState.CLOSED:
        return False, False
    if row.state == CircuitState.HALTED:
        return True, False
    if row.open_until and now >= row.open_until:
        return False, True
    return True, False


def succeeded(key: str) -> None:
    """The partner answered (whatever it said): the line works."""
    with tenant_transaction():
        row = ConnectorCircuit.objects.select_for_update().filter(
            connector_key=key, **_scope()).first()
        if row is None or (row.state == CircuitState.CLOSED and row.failures == 0):
            return
        if row.state == CircuitState.HALTED:
            return                              # only a person lifts a halt
        row.state, row.failures, row.open_until, row.cooldown_seconds = (
            CircuitState.CLOSED, 0, None, 0)
        row.save(update_fields=["state", "failures", "open_until", "cooldown_seconds",
                                "updated_at"])
        record(actor_ref="system", action="connector.circuit_closed", object_type="connector",
               object_ref=key[:64], outcome=AuditOutcome.ALLOWED)


def failed(key: str, now: dt.datetime, code: str) -> None:
    """A call did not get an answer. Open the circuit at the threshold, or again after a trial."""
    with tenant_transaction():
        row = _row(key)
        if row.state == CircuitState.HALTED:
            return
        row.failures += 1
        row.last_code = code[:64]
        trial_failed = row.state == CircuitState.OPEN and row.open_until and now >= row.open_until
        if trial_failed or row.failures >= THRESHOLD:
            seconds = (FIRST_COOLDOWN.total_seconds() if not row.cooldown_seconds
                       else min(row.cooldown_seconds * 2, MAX_COOLDOWN.total_seconds()))
            row.state, row.cooldown_seconds = CircuitState.OPEN, int(seconds)
            row.open_until = now + dt.timedelta(seconds=seconds)
            record(actor_ref="system", action="connector.circuit_opened",
                   object_type="connector", object_ref=key[:64], outcome=AuditOutcome.ALLOWED,
                   reason_code=row.last_code)
        row.save()


def halt(key: str, code: str) -> None:
    """The partner cannot handle what we send: stop this connector until a person resets it."""
    with tenant_transaction():
        row = _row(key)
        if row.state == CircuitState.HALTED:
            return
        row.state, row.last_code, row.open_until = CircuitState.HALTED, code[:64], None
        row.save()
        record(actor_ref="system", action="connector.halted", object_type="connector",
               object_ref=key[:64], outcome=AuditOutcome.ALLOWED, reason_code=row.last_code)


def reset(key: str, *, actor_ref: str) -> dict[str, Any]:
    """A person closes the circuit (after the partner fixed what was wrong)."""
    with tenant_transaction():
        row = ConnectorCircuit.objects.select_for_update().filter(
            connector_key=key, **_scope()).first()
        if row is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        row.state, row.failures, row.open_until, row.cooldown_seconds = (
            CircuitState.CLOSED, 0, None, 0)
        row.save()
        record(actor_ref=actor_ref, action="connector.circuit_reset", object_type="connector",
               object_ref=key[:64], outcome=AuditOutcome.ALLOWED)
        return circuit_dict(row)


def circuit_dict(row: ConnectorCircuit, clock: Clock | None = None) -> dict[str, Any]:
    now = utc_now(clock)
    state = row.state
    if state == CircuitState.OPEN and row.open_until and now >= row.open_until:
        state = "trial"
    return {"connector_key": row.connector_key, "state": state, "failures": row.failures,
            "last_code": row.last_code,
            "open_until": row.open_until.isoformat() if row.open_until else None}


def circuits(clock: Clock | None = None) -> list[dict[str, Any]]:
    return [circuit_dict(r, clock) for r in
            ConnectorCircuit.objects.filter(**_scope()).order_by("connector_key")]


def is_down(key: str, clock: Clock | None = None) -> bool:
    """True while the connector is open (not yet in trial) or halted: the alternate path applies."""
    return bool(key) and blocked(key, utc_now(clock))[0]
