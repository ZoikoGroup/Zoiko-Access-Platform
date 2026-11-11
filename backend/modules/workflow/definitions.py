"""Workflow definitions (GHE-12): versioned, hashed and immutable once released.

A definition is plain data. It names the states, the transitions between them and what each needs
(who may act, what evidence), the timers that start when a state is entered, and which states end
the journey. Running instances pin the version they started on, so a newer definition never changes
a journey already under way.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from foundation.errors import ErrorCode, ZoikoError

# The four kinds of evidence never collapse into one another (docs/schema README rule 8): a
# technical receipt is not a view, a view is not a professional's acknowledgment, and none of
# them is the source's own confirmation of the outcome.
EVIDENCE_KINDS = (
    "transport_receipt", "authenticated_view", "professional_acknowledgment",
    "source_confirmed_outcome",
)
ACTOR_KINDS = ("person", "clinician", "partner", "system")

# Who may supply each kind of evidence. A system process can never supply a professional's
# acknowledgment, and only the source (a partner) can confirm its own outcome.
EVIDENCE_ACTORS: dict[str, frozenset[str]] = {
    "transport_receipt": frozenset({"system", "partner"}),
    "authenticated_view": frozenset({"person", "clinician"}),
    "professional_acknowledgment": frozenset({"clinician"}),
    "source_confirmed_outcome": frozenset({"partner"}),
}

GUARDS = ("actor_is_owner", "human_actor")
TERMINAL_OUTCOMES = ("completed", "cancelled")
MAX_MINUTES = 525_600            # a year

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def digest_of(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _names(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _bad(field)
    return value


def validate_definition(body: Any) -> dict[str, Any]:
    """Refuse a definition that could not run safely. Returns it unchanged when it is sound."""
    if not isinstance(body, dict):
        raise _bad("definition")
    key, version = body.get("key"), body.get("version")
    if not isinstance(key, str) or not _KEY.match(key):
        raise _bad("key")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise _bad("version")
    states = body.get("states")
    if not isinstance(states, dict) or not states or not all(_NAME.match(s) for s in states):
        raise _bad("states")
    initial = body.get("initial")
    if not isinstance(initial, str) or initial not in states:
        raise _bad("initial")

    terminal = body.get("terminal", {})
    if not isinstance(terminal, dict) or not terminal:
        raise _bad("terminal")          # a journey that can never end cannot be closed
    for state, outcome in terminal.items():
        if state not in states or outcome not in TERMINAL_OUTCOMES:
            raise _bad("terminal")

    transitions = body.get("transitions")
    if not isinstance(transitions, dict) or not transitions:
        raise _bad("transitions")
    edges: dict[str, set[str]] = {s: set() for s in states}
    for name, spec in transitions.items():
        if not _NAME.match(name) or not isinstance(spec, dict):
            raise _bad("transitions")
        sources = _names(spec.get("from"), "transitions")
        target = spec.get("to")
        if (not sources or any(s not in states for s in sources)
                or not isinstance(target, str) or target not in states):
            raise _bad("transitions")
        if any(s in terminal for s in sources):
            raise _bad("transitions")   # nothing leaves a finished journey
        for guard in _names(spec.get("guards", []), "guards"):
            if guard not in GUARDS:
                raise _bad("guards")
        for kind in _names(spec.get("requires_evidence", []), "requires_evidence"):
            if kind not in EVIDENCE_KINDS:
                raise _bad("requires_evidence")
        for source in sources:
            edges[source].add(target)

    seen, todo = {initial}, [initial]
    while todo:
        for nxt in edges[todo.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    if set(states) - seen or not set(terminal) & seen:
        raise _bad("states")            # an unreachable state, or no way to finish

    timers = body.get("timers", {})
    if not isinstance(timers, dict):
        raise _bad("timers")
    for name, spec in timers.items():
        minutes = spec.get("after_minutes") if isinstance(spec, dict) else None
        if (not _NAME.match(name) or spec.get("state") not in states
                or isinstance(minutes, bool) or not isinstance(minutes, int)
                or not 1 <= minutes <= MAX_MINUTES):
            raise _bad("timers")
    return body
