"""A pretend partner, for building and testing without a contract. Development and tests only.

It is controlled by the request's `sim` value, so one operation can be made to behave in each of the
ways a real partner can go wrong:

  confirm       answers "received"; a later question gets "done"           (the normal case)
  confirm_now   answers "done" straight away
  reject        answers "no" (reason code `slot_taken`)
  down          refuses the call (`sim_fail` times), then behaves like `confirm_now`
  timeout       does the work but the answer is lost; a question reveals it was done
  timeout_lost  the first call is lost before it arrives (a question finds nothing); sending it
                again works
  silent        answers "received" and never gets any further
"""
from __future__ import annotations

from typing import Any

from .adapters import (
    AdapterTimeout,
    AdapterUnavailable,
    OperationView,
    Outcome,
    PartnerResult,
)


class SimulatorAdapter:
    key = "simulator"
    idempotent = True

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.calls: list[tuple[str, str]] = []              # (what, idempotency key)
        self.ledger: dict[str, Outcome] = {}                # what the "partner" really did
        self._failures: dict[str, int] = {}
        self._seen: dict[str, int] = {}

    def _record(self, what: str, operation: OperationView) -> None:
        self.calls.append((what, operation.idempotency_key))

    def submit(self, operation: OperationView) -> PartnerResult:
        self._record("submit", operation)
        sim = str(operation.request.get("sim", "confirm"))
        key = operation.idempotency_key
        ref = f"SIM-{key[-8:]}"
        self._seen[key] = self._seen.get(key, 0) + 1
        if key in self.ledger:
            return PartnerResult(self.ledger[key], ref)      # the partner honours our key
        if sim == "down":
            wanted = self._int(operation.request, "sim_fail")
            self._failures[key] = self._failures.get(key, 0) + 1
            if self._failures[key] <= wanted:
                raise AdapterUnavailable("the simulated partner is down")
            self.ledger[key] = Outcome.CONFIRMED
            return PartnerResult(Outcome.CONFIRMED, ref)
        if sim == "reject":
            self.ledger[key] = Outcome.REJECTED
            return PartnerResult(Outcome.REJECTED, ref, "slot_taken")
        if sim == "confirm_now":
            self.ledger[key] = Outcome.CONFIRMED
            return PartnerResult(Outcome.CONFIRMED, ref)
        if sim == "timeout":
            self.ledger[key] = Outcome.CONFIRMED             # it happened; we just never heard
            raise AdapterTimeout("the simulated partner did not answer")
        if sim == "timeout_lost":
            if self._seen[key] == 1:
                raise AdapterTimeout("the simulated partner did not answer")
            self.ledger[key] = Outcome.CONFIRMED             # sent again: it arrives this time
            return PartnerResult(Outcome.CONFIRMED, ref)
        if sim == "silent":
            self.ledger[key] = Outcome.ACCEPTED
            return PartnerResult(Outcome.ACCEPTED, ref)
        self.ledger[key] = Outcome.ACCEPTED                  # "confirm": received for now
        return PartnerResult(Outcome.ACCEPTED, ref)

    def query(self, operation: OperationView) -> PartnerResult:
        self._record("query", operation)
        sim = str(operation.request.get("sim", "confirm"))
        known = self.ledger.get(operation.idempotency_key)
        if known is None:
            return PartnerResult(Outcome.NOT_FOUND)
        ref = f"SIM-{operation.idempotency_key[-8:]}"
        if known is Outcome.ACCEPTED:
            if sim == "silent":
                return PartnerResult(Outcome.PENDING, ref)
            self.ledger[operation.idempotency_key] = Outcome.CONFIRMED
            return PartnerResult(Outcome.CONFIRMED, ref)
        return PartnerResult(known, ref)

    @staticmethod
    def _int(request: dict[str, Any], name: str) -> int:
        value = request.get(name, 1)
        return value if isinstance(value, int) and not isinstance(value, bool) else 1


simulator = SimulatorAdapter()
