"""The standard lifecycles of a booking and of a referral, as the product specification defines them
(GHE-14 section 06 and WF-17): the states, the steps that may follow each, and a plain-words label.

The stored record keeps its own simple status (requested, confirmed, cancelled; issued, accepted,
declined). These lifecycles are the one vocabulary every screen, partner message and report uses on
top of it: `booking_state` and `referral_state` read the facts a record already holds and say where
it stands. They are pure functions: no database, no clock, nothing to keep in step.

Two rules from the specification run through both:
  * a technical acknowledgement is never "confirmed" or "accepted": only the provider's own answer
    is, so a request the partner merely received stays PENDING_SOURCE / SENT;
  * nothing becomes final by waiting: a request that has gone quiet is ESCALATED or TIMED_OUT for a
    person to deal with, never quietly confirmed or dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Step:
    key: str
    label: str            # for the person looking at the screen
    final: bool = False   # nothing follows unless a new request is made


def _index(steps: tuple[Step, ...]) -> dict[str, Step]:
    return {s.key: s for s in steps}


# --------------------------------------------------------------------------- bookings
BOOKING_STEPS = (
    Step("DRAFT", "Being prepared"),
    Step("VALIDATING", "Being checked"),
    Step("REQUEST_SUBMITTED", "Request sent to the clinic"),
    Step("PENDING_SOURCE", "Waiting for the clinic's answer"),
    Step("CONFIRMED", "Confirmed"),
    Step("REJECTED", "Not accepted", final=True),
    Step("OUTCOME_UNKNOWN", "Waiting: we do not know the answer yet"),
    Step("RECONCILING", "Being checked with the clinic's system"),
    Step("TIMED_OUT", "No answer in time"),
    Step("CHANGE_REQUESTED", "A change was asked for"),
    Step("SOURCE_CONFIRMED_NEW", "Moved, and the new time is confirmed"),
    Step("CANCEL_REQUESTED", "A cancellation was asked for"),
    Step("SOURCE_CANCEL_CONFIRMED", "Cancelled", final=True),
    Step("ATTENDED", "Attended", final=True),
    Step("MISSED", "Not attended", final=True),
)
BOOKING_STATES = _index(BOOKING_STEPS)

BOOKING_NEXT: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"VALIDATING"}),
    "VALIDATING": frozenset({"REQUEST_SUBMITTED", "REJECTED"}),
    "REQUEST_SUBMITTED": frozenset({"PENDING_SOURCE"}),
    "PENDING_SOURCE": frozenset({"CONFIRMED", "REJECTED", "OUTCOME_UNKNOWN", "TIMED_OUT"}),
    "OUTCOME_UNKNOWN": frozenset({"RECONCILING"}),
    "TIMED_OUT": frozenset({"RECONCILING"}),          # requires safe reconciliation first
    "RECONCILING": frozenset({"CONFIRMED", "REJECTED"}),
    "CONFIRMED": frozenset({"CHANGE_REQUESTED", "CANCEL_REQUESTED", "ATTENDED", "MISSED"}),
    "CHANGE_REQUESTED": frozenset({"SOURCE_CONFIRMED_NEW", "CONFIRMED"}),
    "SOURCE_CONFIRMED_NEW": frozenset(
        {"CHANGE_REQUESTED", "CANCEL_REQUESTED", "ATTENDED", "MISSED"}),
    "CANCEL_REQUESTED": frozenset({"SOURCE_CANCEL_CONFIRMED", "CONFIRMED"}),
}


def booking_state(
    *, status: str, attendance: str | None = None, pending_change: str | None = None,
    rescheduled_from: str | None = None, provider_stage: dict[str, Any] | None = None,
) -> str:
    """Where a booking stands in the standard lifecycle, from the facts the booking holds.

    `provider_stage` is what the clinic's booking system last said ({"stage", "operation"}), or
    None when staff confirm by hand. A technical receipt ("accepted") never counts as confirmed."""
    if status == "cancelled":
        if (provider_stage or {}).get("stage") == "refused":
            return "REJECTED"
        return "SOURCE_CANCEL_CONFIRMED"
    if status == "confirmed":
        if attendance == "attended":
            return "ATTENDED"
        if attendance == "no_show":
            return "MISSED"
        if pending_change == "cancel":
            return "CANCEL_REQUESTED"
        if pending_change == "rebook":
            return "CHANGE_REQUESTED"
        return "SOURCE_CONFIRMED_NEW" if rescheduled_from else "CONFIRMED"
    # Still requested: the question is who answers, and whether we have heard.
    if provider_stage is None:
        return "REQUEST_SUBMITTED"              # waiting for the clinic's own staff
    operation = provider_stage.get("operation", "")
    if operation == "unknown":
        return "OUTCOME_UNKNOWN"
    if operation == "dead":
        return "TIMED_OUT"
    if operation == "rejected":
        return "REJECTED"
    return "PENDING_SOURCE"                     # pending, sent, or only acknowledged


# --------------------------------------------------------------------------- referrals
REFERRAL_STEPS = (
    Step("ORDER_ISSUED", "Referral written"),
    Step("ELIGIBILITY_PASS", "Checked: the service can take it"),
    Step("SENT", "Sent to the receiving service"),
    Step("TRANSPORT_ACK", "Received by the service's office"),
    Step("INTAKE_PENDING", "Waiting for a clinician to decide"),
    Step("ACCEPTED", "Accepted by the receiving clinician", final=True),
    Step("NEEDS_INFO", "The receiving service needs more information"),
    Step("REJECTED", "Declined by the receiving clinician", final=True),
    Step("NOT_ACCEPTED", "Received but not taken on", final=True),
    Step("ESCALATED", "No answer in time: passed to the named clinical owner"),
)
REFERRAL_STATES = _index(REFERRAL_STEPS)

REFERRAL_NEXT: dict[str, frozenset[str]] = {
    "ORDER_ISSUED": frozenset({"ELIGIBILITY_PASS"}),
    "ELIGIBILITY_PASS": frozenset({"SENT"}),
    "SENT": frozenset({"TRANSPORT_ACK", "ESCALATED"}),
    "TRANSPORT_ACK": frozenset({"INTAKE_PENDING", "NOT_ACCEPTED"}),
    "INTAKE_PENDING": frozenset({"ACCEPTED", "REJECTED", "NEEDS_INFO", "ESCALATED"}),
    "NEEDS_INFO": frozenset({"INTAKE_PENDING", "ACCEPTED", "REJECTED", "ESCALATED"}),
    "ESCALATED": frozenset({"INTAKE_PENDING", "ACCEPTED", "REJECTED", "NEEDS_INFO"}),
}
# Clinical responsibility never shifts because a state was reached: it stays with the referrer until
# the receiving clinician accepts (the caller reads this, it is not enforced here).
RESPONSIBILITY_MOVES_ON = frozenset({"ACCEPTED"})


def referral_state(*, state: str, acknowledged: bool, escalated: bool) -> str:
    """Where a referral stands in the standard lifecycle. `state` is the referral's own status
    (issued, needs_info, accepted, declined); `acknowledged` means the receiving office has seen
    it; `escalated` means it has waited past the receiving clinic's own response target."""
    if state == "accepted":
        return "ACCEPTED"
    if state == "declined":
        return "REJECTED"
    if state == "needs_info":
        return "NEEDS_INFO"
    if escalated:
        return "ESCALATED"
    return "INTAKE_PENDING" if acknowledged else "SENT"


def referral_timeline(
    *, state: str, created_at: str, acknowledged_at: str | None, decided_at: str | None,
    escalated: bool,
) -> list[dict[str, str | None]]:
    """The steps a referral has been through, oldest first, from the times it holds. The first
    three happen together when it is sent, and receipt and intake together when the office sees it.
    An escalation has no time of its own (it is a rule applied to the clock), so it shows none."""
    steps: list[tuple[str, str | None]] = [
        ("ORDER_ISSUED", created_at), ("ELIGIBILITY_PASS", created_at), ("SENT", created_at)]
    if acknowledged_at:
        steps += [("TRANSPORT_ACK", acknowledged_at), ("INTAKE_PENDING", acknowledged_at)]
    if escalated and state in ("issued", "needs_info"):
        steps.append(("ESCALATED", None))
    final = {"accepted": "ACCEPTED", "declined": "REJECTED", "needs_info": "NEEDS_INFO"}.get(state)
    if final:
        steps.append((final, decided_at))
    return [{"state": key, "label": REFERRAL_STATES[key].label, "at": at} for key, at in steps]


def describe(states: dict[str, Step], nexts: dict[str, frozenset[str]], key: str) -> dict[str, Any]:
    """What a screen needs about one state: its key, plain label, whether it is the end, and the
    states that may follow it (in a fixed order, so the same answer every time)."""
    step = states[key]
    return {
        "state": key, "label": step.label, "final": step.final,
        "next": [k for k in states if k in nexts.get(key, frozenset())],
    }


def booking_lifecycle(**facts: Any) -> dict[str, Any]:
    return describe(BOOKING_STATES, BOOKING_NEXT, booking_state(**facts))


def referral_lifecycle(**facts: Any) -> dict[str, Any]:
    return describe(REFERRAL_STATES, REFERRAL_NEXT, referral_state(**facts))


def can_move(nexts: dict[str, frozenset[str]], here: str, there: str) -> bool:
    """Is `there` a step that may follow `here`? (Used by tests, and by anything that records a
    step, so an impossible jump is refused rather than stored.)"""
    return there in nexts.get(here, frozenset())
