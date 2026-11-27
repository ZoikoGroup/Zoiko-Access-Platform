"""Asking an insurer whether a policy covers something (GHE-17), through a plug-in point.

An insurer is reached through an adapter that knows how to ask THAT insurer. None is built into the
product for a real insurer: a clinic's insurer with no adapter gets the honest answer "unknown: we
cannot check with this insurer, ask them", never a guess. A real adapter registers itself under the
insurer's name with `register_adapter`.

A pretend insurer is included for development and for the tests, switched on only by the
COVER_SIMULATOR setting (the release check refuses it on a real server). It answers from the LAST
LETTERS of the member number so every outcome can be tried:

    ...OK  eligible        ...EX  expired       ...NC  not covered for this category
    ...NF  member unknown  ...DOWN  unavailable  anything else: unclear, so "unknown"

The answer is indicative: it says what the insurer said at that moment, never that a claim will be
paid. The member number goes to the adapter only for the call; it is never stored with the answer.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings

ELIGIBLE, NOT_ELIGIBLE, UNKNOWN = "eligible", "not_eligible", "unknown"
RESULTS = (ELIGIBLE, NOT_ELIGIBLE, UNKNOWN)

# Why, in words a patient can read. A fixed list, so no insurer text (and no clinical detail) is
# ever shown or stored.
REASONS: dict[str, str] = {
    "in_force": "The insurer says the policy is in force.",
    "not_covered": "The insurer says this is not covered by the policy.",
    "expired": "The policy has run out.",
    "member_not_found": "The insurer does not know this member number.",
    "no_connection": "We cannot check with this insurer automatically. Please ask them.",
    "unavailable": "The insurer could not be reached. Please try again later.",
    "unclear": "The insurer's answer was not clear. Please ask them.",
}


class InsurerUnavailable(RuntimeError):
    """The insurer could not be reached or did not answer in time."""


@dataclass(frozen=True)
class Answer:
    result: str
    reason: str
    source_ref: str = ""             # the insurer's own reference for this answer, if it gave one


class InsurerAdapter:
    name = ""

    def check(self, *, member_id: str, category: str, on: dt.date) -> Answer:  # pragma: no cover
        raise NotImplementedError


class SimulatedInsurer(InsurerAdapter):
    """Development only. See the module note for how it answers."""

    name = "simulator"

    def check(self, *, member_id: str, category: str, on: dt.date) -> Answer:
        ref = "SIM-" + hashlib.sha256(f"{member_id}|{category}|{on}".encode()).hexdigest()[:8]
        end = member_id.upper()
        if end.endswith("DOWN"):
            raise InsurerUnavailable
        if end.endswith("OK"):
            return Answer(ELIGIBLE, "in_force", ref)
        if end.endswith("EX"):
            return Answer(NOT_ELIGIBLE, "expired", ref)
        if end.endswith("NC"):
            return Answer(NOT_ELIGIBLE, "not_covered", ref)
        if end.endswith("NF"):
            return Answer(NOT_ELIGIBLE, "member_not_found", ref)
        return Answer(UNKNOWN, "unclear", ref)


_ADAPTERS: dict[str, Callable[[], InsurerAdapter]] = {}


def _key(insurer_name: str) -> str:
    return " ".join((insurer_name or "").lower().split())


def register_adapter(insurer_name: str, factory: Callable[[], InsurerAdapter]) -> None:
    """Use this adapter whenever a policy's insurer has this name (case and spacing ignored)."""
    _ADAPTERS[_key(insurer_name)] = factory


def reset_adapters() -> None:
    _ADAPTERS.clear()


def adapter_for(insurer_name: str) -> InsurerAdapter | None:
    """The adapter that can ask this insurer, or None (then the answer is "unknown")."""
    key = _key(insurer_name)
    if key in _ADAPTERS:
        return _ADAPTERS[key]()
    if getattr(settings, "COVER_SIMULATOR", False) and key.startswith("pretend insurer"):
        return SimulatedInsurer()
    return None
