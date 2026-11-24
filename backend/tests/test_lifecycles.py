"""The standard lifecycles of a booking and of a referral (foundation/lifecycles.py): the graph, the
reading of a record's facts into a state, and what the API shows."""
import json

import pytest

from foundation import lifecycles as lc
from foundation.lifecycles import (
    BOOKING_NEXT,
    BOOKING_STATES,
    REFERRAL_NEXT,
    REFERRAL_STATES,
    booking_lifecycle,
    booking_state,
    can_move,
    referral_lifecycle,
    referral_state,
    referral_timeline,
)


# ------------------------------------------------------------------ the graph (no database)
@pytest.mark.parametrize("states, nexts, start", [
    (BOOKING_STATES, BOOKING_NEXT, "DRAFT"), (REFERRAL_STATES, REFERRAL_NEXT, "ORDER_ISSUED")])
def test_every_step_follows_from_the_start_and_only_known_steps_are_named(states, nexts, start):
    assert set(nexts) <= set(states)
    for after in nexts.values():
        assert after <= set(states)
    seen, todo = {start}, [start]
    while todo:
        for nxt in nexts.get(todo.pop(), frozenset()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    # A state nothing leads to would be dead weight in the vocabulary. The referral list holds
    # exactly one such state, NOT_ACCEPTED, which a receiving office reaches from TRANSPORT_ACK.
    assert seen == set(states)


@pytest.mark.parametrize("states, nexts", [
    (BOOKING_STATES, BOOKING_NEXT), (REFERRAL_STATES, REFERRAL_NEXT)])
def test_a_final_step_leads_nowhere_and_every_other_step_leads_somewhere(states, nexts):
    for key, step in states.items():
        if step.final:
            assert nexts.get(key, frozenset()) == frozenset(), key
        else:
            assert nexts.get(key), key
        assert step.label.strip(), key


def test_the_specification_s_booking_path_is_allowed_and_the_shortcuts_are_not():
    walk = ["DRAFT", "VALIDATING", "REQUEST_SUBMITTED", "PENDING_SOURCE", "CONFIRMED",
            "CANCEL_REQUESTED", "SOURCE_CANCEL_CONFIRMED"]
    assert all(can_move(BOOKING_NEXT, a, b) for a, b in zip(walk, walk[1:], strict=False))
    assert not can_move(BOOKING_NEXT, "PENDING_SOURCE", "SOURCE_CANCEL_CONFIRMED")
    assert not can_move(BOOKING_NEXT, "REQUEST_SUBMITTED", "CONFIRMED")   # no confirming unasked
    assert not can_move(BOOKING_NEXT, "OUTCOME_UNKNOWN", "CONFIRMED")     # reconcile first
    assert not can_move(BOOKING_NEXT, "TIMED_OUT", "CONFIRMED")
    assert can_move(BOOKING_NEXT, "OUTCOME_UNKNOWN", "RECONCILING")
    assert can_move(BOOKING_NEXT, "RECONCILING", "CONFIRMED")


def test_the_specification_s_referral_path_is_allowed_and_responsibility_never_shifts_early():
    walk = ["ORDER_ISSUED", "ELIGIBILITY_PASS", "SENT", "TRANSPORT_ACK", "INTAKE_PENDING",
            "ACCEPTED"]
    assert all(can_move(REFERRAL_NEXT, a, b) for a, b in zip(walk, walk[1:], strict=False))
    assert not can_move(REFERRAL_NEXT, "SENT", "ACCEPTED")                # an ack is not acceptance
    assert not can_move(REFERRAL_NEXT, "TRANSPORT_ACK", "ACCEPTED")
    assert not can_move(REFERRAL_NEXT, "ESCALATED", "ESCALATED")
    assert lc.RESPONSIBILITY_MOVES_ON == {"ACCEPTED"}


# ------------------------------------------------------------------ reading a booking
def waiting(operation):
    """What the clinic's booking system last said while the booking is still requested."""
    return {"stage": "awaiting_provider", "operation": operation}


@pytest.mark.parametrize("facts, expected", [
    (dict(status="requested"), "REQUEST_SUBMITTED"),                       # the clinic confirms
    (dict(status="requested", provider_stage=waiting("pending")),
     "PENDING_SOURCE"),
    (dict(status="requested", provider_stage=waiting("sent")),
     "PENDING_SOURCE"),
    (dict(status="requested",
          provider_stage=waiting("accepted")),
     "PENDING_SOURCE"),                                  # a technical receipt is NOT confirmed
    (dict(status="requested", provider_stage=waiting("unknown")),
     "OUTCOME_UNKNOWN"),
    (dict(status="requested", provider_stage=waiting("dead")),
     "TIMED_OUT"),
    (dict(status="requested", provider_stage=waiting("rejected")),
     "REJECTED"),
    (dict(status="confirmed"), "CONFIRMED"),
    (dict(status="confirmed", rescheduled_from="oldbooking"), "SOURCE_CONFIRMED_NEW"),
    (dict(status="confirmed", pending_change="cancel"), "CANCEL_REQUESTED"),
    (dict(status="confirmed", pending_change="rebook"), "CHANGE_REQUESTED"),
    (dict(status="confirmed", attendance="attended"), "ATTENDED"),
    (dict(status="confirmed", attendance="no_show"), "MISSED"),
    (dict(status="cancelled"), "SOURCE_CANCEL_CONFIRMED"),
    (dict(status="cancelled", provider_stage={"stage": "refused", "operation": ""}), "REJECTED"),
])
def test_a_booking_is_read_into_its_standard_state(facts, expected):
    assert booking_state(**facts) == expected


def test_a_booking_lifecycle_says_what_may_follow_in_a_fixed_order():
    done = booking_lifecycle(status="confirmed")
    assert done["state"] == "CONFIRMED" and done["label"] == "Confirmed" and done["final"] is False
    assert done["next"] == ["CHANGE_REQUESTED", "CANCEL_REQUESTED", "ATTENDED", "MISSED"]
    ended = booking_lifecycle(status="cancelled")
    assert ended["final"] is True and ended["next"] == []


def test_only_the_providers_own_answer_ever_shows_a_booking_as_confirmed():
    for operation in ("pending", "sent", "accepted", "unknown", "dead", "rejected", ""):
        state = booking_state(status="requested", provider_stage=waiting(operation))
        assert state != "CONFIRMED", operation


# ------------------------------------------------------------------ reading a referral
@pytest.mark.parametrize("facts, expected", [
    (dict(state="issued", acknowledged=False, escalated=False), "SENT"),
    (dict(state="issued", acknowledged=True, escalated=False), "INTAKE_PENDING"),
    (dict(state="issued", acknowledged=False, escalated=True), "ESCALATED"),
    (dict(state="issued", acknowledged=True, escalated=True), "ESCALATED"),
    (dict(state="needs_info", acknowledged=True, escalated=False), "NEEDS_INFO"),
    (dict(state="needs_info", acknowledged=True, escalated=True), "NEEDS_INFO"),
    (dict(state="accepted", acknowledged=True, escalated=False), "ACCEPTED"),
    (dict(state="accepted", acknowledged=False, escalated=True), "ACCEPTED"),
    (dict(state="declined", acknowledged=True, escalated=False), "REJECTED"),
])
def test_a_referral_is_read_into_its_standard_state(facts, expected):
    assert referral_state(**facts) == expected


def test_a_referral_that_waits_too_long_is_escalated_never_accepted():
    assert referral_lifecycle(state="issued", acknowledged=False, escalated=True)["state"] == (
        "ESCALATED")
    assert referral_lifecycle(state="issued", acknowledged=False, escalated=True)["next"] == [
        "INTAKE_PENDING", "ACCEPTED", "NEEDS_INFO", "REJECTED"]


def test_the_timeline_lists_the_steps_a_referral_went_through_in_order():
    sent = referral_timeline(state="issued", created_at="T1", acknowledged_at=None,
                             decided_at=None, escalated=False)
    assert [s["state"] for s in sent] == ["ORDER_ISSUED", "ELIGIBILITY_PASS", "SENT"]
    done = referral_timeline(state="accepted", created_at="T1", acknowledged_at="T2",
                             decided_at="T3", escalated=False)
    assert [(s["state"], s["at"]) for s in done] == [
        ("ORDER_ISSUED", "T1"), ("ELIGIBILITY_PASS", "T1"), ("SENT", "T1"),
        ("TRANSPORT_ACK", "T2"), ("INTAKE_PENDING", "T2"), ("ACCEPTED", "T3")]
    late = referral_timeline(state="issued", created_at="T1", acknowledged_at=None,
                             decided_at=None, escalated=True)
    assert late[-1] == {"state": "ESCALATED", "label": REFERRAL_STATES["ESCALATED"].label,
                        "at": None}
    assert all(step["label"] for step in done)


# ------------------------------------------------------------------ what the API shows
@pytest.fixture
def db_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("rls_probe", "db_login")
def test_a_booking_shows_its_lifecycle_as_the_clinic_confirms_then_cancels_it():
    from tests.test_api import book
    from tests.test_clinician import Practice

    p = Practice()
    p.add_times(p.doc, count=2)
    made = book(p, p.slot_of("doc"), subject="pat")
    assert made.status_code == 201, made.content
    booking = json.loads(made.content)["booking"]
    assert booking["lifecycle"]["state"] == "REQUEST_SUBMITTED"
    confirmed = json.loads(p.post(f"bookings/{booking['id']}/confirm", subject="ops").content)
    assert confirmed["booking"]["lifecycle"] == booking_lifecycle(status="confirmed")
    # A patient cancelling a CONFIRMED appointment only asks: the clinic still has to agree.
    asked = json.loads(p.post(f"bookings/{booking['id']}/cancel", subject="pat").content)
    assert asked["booking"]["lifecycle"]["state"] == "CANCEL_REQUESTED"
    assert asked["booking"]["lifecycle"]["final"] is False
    cancelled = json.loads(p.post(f"bookings/{booking['id']}/cancel", subject="ops").content)
    assert cancelled["booking"]["lifecycle"]["state"] == "SOURCE_CANCEL_CONFIRMED"
    assert cancelled["booking"]["lifecycle"]["final"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("rls_probe", "db_login")
def test_a_referral_shows_its_lifecycle_and_timeline_from_sent_to_accepted():
    from tests.test_referral import Clinic2, issued

    c = Clinic2()
    made = issued(c)
    assert made["lifecycle"]["state"] == "SENT"
    assert [s["state"] for s in made["timeline"]] == ["ORDER_ISSUED", "ELIGIBILITY_PASS", "SENT"]
    assert c.post(f"referrals/{made['id']}/acknowledge", {}, subject="ops").status_code == 200
    (seen,) = [r for r in c.incoming() if r["id"] == made["id"]]
    assert seen["lifecycle"]["state"] == "INTAKE_PENDING"
    assert c.decide(made["id"], "accepted").status_code == 200
    (done,) = [r for r in c.incoming() if r["id"] == made["id"]]
    assert done["lifecycle"]["state"] == "ACCEPTED" and done["lifecycle"]["final"] is True
    assert done["timeline"][-1]["state"] == "ACCEPTED" and done["timeline"][-1]["at"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("rls_probe", "db_login")
def test_the_booking_intent_is_never_worded_as_confirmed_until_the_clinic_answers():
    from tests.test_api import API, book
    from tests.test_clinician import Practice

    p = Practice()
    p.add_times(p.doc, count=2)
    booking = json.loads(book(p, p.slot_of("doc"), subject="pat").content)["booking"]["id"]

    def intent(subject="pat"):
        r = p.get(f"bookings/{booking}/intent", subject=subject)
        assert r.status_code == 200, r.content
        return json.loads(r.content)

    waiting = intent()
    assert waiting["intent_id"] == booking and waiting["status"] == "REQUEST_SUBMITTED"
    assert waiting["source_confirmation"] is None and waiting["final"] is False
    assert intent("ops")["status"] == "REQUEST_SUBMITTED"            # the clinic sees the same
    p.post(f"bookings/{booking}/confirm", subject="ops")
    done = intent()
    assert done["status"] == "CONFIRMED" and done["source_confirmation"] == {"by": "clinic"}
    assert done["next"] == ["CHANGE_REQUESTED", "CANCEL_REQUESTED", "ATTENDED", "MISSED"]
    p.patient("other")
    assert p.get(f"bookings/{booking}/intent", subject="other").status_code in (403, 404)
    assert p.client.get(API + f"bookings/{booking}/intent").status_code == 401
    assert p.post(f"bookings/{booking}/intent", {}, subject="pat").status_code == 405
