"""A waiting-list request's journey (J3.waitlist): waiting, offered a time, then booked or left.

The waitlist module still decides everything (who is offered which time, when an unused offer goes
back to waiting). This only records the journey beside it, from the waitlist events, so a request
appears on the Journeys and Exceptions screens and one that waits far too long raises an exception.

What it keeps apart:
  * an offer and an offer going back to waiting are done by the product (the system), never by a
    person; booking and leaving are done by a person, and the journey refuses the system for them;
  * a timer only raises an exception: it never closes a request or offers a time;
  * a request that joined before this existed has no journey, and nothing here invents one for it.

Runs in the event-delivery worker, in the event's tenant. Events carry only ids.
"""
from __future__ import annotations

from typing import Any

from foundation.errors import ErrorCode, ZoikoError
from modules.waitlist import api as waitlist

from .engine import engine, ensure_bundled_definitions, running_instance_for

DEFINITION = "J3.waitlist"
SYSTEM_ACTOR = "system"


def _entry(entry_ref: str) -> dict[str, Any] | None:
    try:
        return waitlist.get_entry(entry_ref)
    except ZoikoError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return None
        raise


def _start(entry_ref: str, organization_ref: str) -> dict[str, Any]:
    existing = running_instance_for(entry_ref, DEFINITION)
    if existing is not None:
        return existing

    def start() -> dict[str, Any]:
        return engine.start(
            definition_key=DEFINITION, subject_ref=entry_ref, owner_ref=organization_ref,
            actor_ref=SYSTEM_ACTOR, organization_ref=organization_ref)

    try:
        return start()
    except ZoikoError as exc:
        if exc.code != ErrorCode.NOT_FOUND:
            raise
        ensure_bundled_definitions()                 # the first request of a new cell
        return start()


def _advance(entry_ref: str, transition: str, actor_ref: str, actor_kind: str) -> bool:
    """Move the journey on if it is running and the step is allowed from where it is. Anything
    else (no journey, already ended, a repeat) means there is nothing to do."""
    instance = running_instance_for(entry_ref, DEFINITION)
    if instance is None:
        return False
    try:
        engine.submit_transition(
            instance_ref=instance["public_id"], transition=transition, actor_ref=actor_ref,
            actor_kind=actor_kind, expected_version=instance["entity_version"])
    except ZoikoError as exc:
        if exc.code in (ErrorCode.VALIDATION_FAILED, ErrorCode.CONFLICT_VERSION):
            return False
        raise
    return True


def on_joined(entry_ref: str) -> None:
    found = _entry(entry_ref)
    if found is not None and found["state"] in ("waiting", "offered"):
        _start(entry_ref, found["organization_ref"])


def on_offered(entry_ref: str) -> None:
    _advance(entry_ref, "offer", SYSTEM_ACTOR, "system")


def on_returned(entry_ref: str) -> None:
    _advance(entry_ref, "return_to_waiting", SYSTEM_ACTOR, "system")


def on_booked(entry_ref: str) -> None:
    found = _entry(entry_ref)
    if found is not None:
        _advance(entry_ref, "book", found["person_ref"], "person")


def on_left(entry_ref: str, actor_ref: str) -> None:
    found = _entry(entry_ref)
    if found is not None:
        _advance(entry_ref, "leave", actor_ref or found["person_ref"], "person")
