"""What a partner adapter looks like, and the registry of the ones this cell may use.

An adapter translates between Zoiko's reference-only request and ONE partner's own protocol (FHIR,
HL7, X12, a private API). It knows how to send an operation and how to ask the partner what became
of it. It never touches the database: the connector service does that.

An adapter signals three different things, and they must not be confused:

  returns a PartnerResult      the partner answered (accepted, confirmed, rejected...)
  raises AdapterUnavailable    the call certainly did NOT reach the partner (connection refused):
                               sending it again is safe
  raises AdapterTimeout        the call went out and no answer came back: the partner MAY have done
                               it. Never blindly sent again; reconciliation asks the partner first
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class Outcome(str, Enum):
    ACCEPTED = "accepted"        # the partner has it (a technical receipt, not a result)
    CONFIRMED = "confirmed"      # the partner says it is done
    REJECTED = "rejected"        # the partner says no
    PENDING = "pending"          # (a question's answer) still being worked on
    NOT_FOUND = "not_found"      # (a question's answer) the partner has no record of it


@dataclass(frozen=True)
class PartnerResult:
    outcome: Outcome
    partner_ref: str = ""        # the partner's own id for the operation
    code: str = ""               # a short reason code from the partner (never free text)


@dataclass(frozen=True)
class OperationView:
    """What an adapter is shown: references and flat request values only."""

    operation_ref: str
    operation_type: str
    subject_ref: str
    idempotency_key: str
    partner_ref: str
    request: dict[str, Any] = field(default_factory=dict)


class AdapterTimeout(Exception):
    """The call went out and no answer came back. The outcome is unknown."""


class AdapterUnavailable(Exception):
    """The call did not reach the partner. Safe to send again."""


class Adapter(Protocol):
    key: str
    # True when the partner honours our idempotency key, so asking twice does the thing once.
    idempotent: bool

    def submit(self, operation: OperationView) -> PartnerResult: ...

    def query(self, operation: OperationView) -> PartnerResult: ...


_adapters: dict[str, Adapter] = {}


def register(adapter: Adapter) -> None:
    """Make an adapter available. Registering the same key again replaces it."""
    _adapters[adapter.key] = adapter


def get(key: str) -> Adapter | None:
    return _adapters.get(key)


def registered() -> list[str]:
    return sorted(_adapters)


def clear() -> None:
    """For tests only."""
    _adapters.clear()
