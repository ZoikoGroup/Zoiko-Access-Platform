"""A referral's journey (J2.referral): sent, seen by the clinic, maybe needing more information,
then accepted or declined.

The referral module still decides everything (who may answer, what the patient can see). This only
records the journey beside it, from the referral events, so a referral appears on the Journeys and
Exceptions screens like any other journey and a stalled one raises an exception.

What it keeps apart, as the product rules ask:
  * "acknowledged" means staff SAW the referral. It is not acceptance of care: only a doctor's
    decision accepts, and that decision carries a clinician's acknowledgment as its evidence;
  * a step by anyone who is not a person (the system, a partner) is refused by the journey itself;
  * a referral sent before this existed has no journey, and nothing here invents one for it.

Runs in the event-delivery worker, in the event's tenant. Events carry only ids.
"""
from __future__ import annotations

from typing import Any

from foundation.errors import ErrorCode, ZoikoError
from modules.referral import api as referral

from .engine import Evidence, engine, ensure_bundled_definitions, running_instance_for

DEFINITION = "J2.referral"
SYSTEM_ACTOR = "system"
# The referral's decision (its new state) -> the journey step, and who is acting.
DECISION_STEPS = {"accepted": "accept", "declined": "decline", "needs_info": "request_info"}


def _start(referral_ref: str, organization_ref: str) -> dict[str, Any]:
    existing = running_instance_for(referral_ref, DEFINITION)
    if existing is not None:
        return existing

    def start() -> dict[str, Any]:
        return engine.start(
            definition_key=DEFINITION, subject_ref=referral_ref, owner_ref=organization_ref,
            actor_ref=SYSTEM_ACTOR, organization_ref=organization_ref)

    try:
        return start()
    except ZoikoError as exc:
        if exc.code != ErrorCode.NOT_FOUND:
            raise
        ensure_bundled_definitions()                 # the first referral of a new cell
        return start()


def _advance(
    referral_ref: str, transition: str, actor_ref: str, actor_kind: str,
    evidence: list[Evidence] | None = None,
) -> bool:
    """Move the journey on if it is running and the step is allowed from where it is. Anything
    else (no journey, already ended, a repeat) means there is nothing to do."""
    instance = running_instance_for(referral_ref, DEFINITION)
    if instance is None:
        return False
    try:
        engine.submit_transition(
            instance_ref=instance["public_id"], transition=transition, actor_ref=actor_ref,
            actor_kind=actor_kind, expected_version=instance["entity_version"], evidence=evidence)
    except ZoikoError as exc:
        if exc.code in (ErrorCode.VALIDATION_FAILED, ErrorCode.CONFLICT_VERSION):
            return False
        raise
    return True


def on_issued(referral_ref: str) -> None:
    try:
        found = referral.get_referral(referral_ref)
    except ZoikoError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return
        raise
    if found["state"] == "issued":
        _start(referral_ref, found["destination_organization_ref"])


def on_acknowledged(referral_ref: str, actor_ref: str) -> None:
    _advance(referral_ref, "acknowledge", actor_ref or SYSTEM_ACTOR, "person")


def on_decided(referral_ref: str, state: str, actor_ref: str) -> None:
    step = DECISION_STEPS.get(state)
    if step is None or not actor_ref:
        return
    if step == "request_info":
        _advance(referral_ref, step, actor_ref, "person")
        return
    # Accepting or declining is a doctor's decision: the journey records it with a clinician's
    # acknowledgment (the decision itself) as evidence.
    _advance(referral_ref, step, actor_ref, "clinician",
             [Evidence("professional_acknowledgment", referral_ref)])
