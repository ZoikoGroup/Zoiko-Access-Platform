"""A referral's journey (J2.referral) kept beside the referral: sent, seen, maybe needs information,
accepted or declined. The referral module still decides; the journey only records and raises
exceptions when a referral stalls."""
import datetime as dt
import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import relay_once
from foundation.timeutil import UTC, FixedClock
from modules.referral.models import Referral
from modules.workflow import api as workflow
from modules.workflow import referral_journey
from tests.test_referral import Clinic2, issued

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

KEY = "J2.referral"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def c():
    return Clinic2()


def pump():
    for _ in range(6):
        relay_once(dispatch.dispatch)


def journey(c, referral_id):
    with tenant_transaction(c.clinic.ctx):
        return workflow.instances_for([referral_id], KEY).get(referral_id)


def steps(c, referral_id):
    with tenant_transaction(c.clinic.ctx):
        return workflow.instance_history(journey(c, referral_id)["public_id"])


def acknowledge(c, referral_id, subject="ops"):
    return c.post(f"referrals/{referral_id}/acknowledge", {}, subject=subject)


def sent(c):
    made = issued(c)
    pump()
    return made


# ------------------------------------------------------------------ the journey starts
def test_sending_a_referral_starts_its_journey_at_the_receiving_clinic(c):
    made = sent(c)
    inst = journey(c, made["id"])
    assert (inst["state"], inst["status"]) == ("sent", "running")
    assert inst["organization_ref"] == c.clinic.org          # the receiving clinic's journey


def test_the_journey_is_listed_with_the_clinics_journeys(c):
    made = sent(c)
    with tenant_transaction(c.clinic.ctx):
        rows = workflow.list_instances(organization_ref=c.clinic.org)
    subjects = {i["subject_ref"] for i in rows}
    assert made["id"] in subjects
    body = json.loads(c.get("staff/journeys", subject="ops").content)
    keys = {d["key"] for d in body["definitions"]}
    assert KEY in keys


def test_a_second_event_for_the_same_referral_does_not_start_a_second_journey(c):
    made = sent(c)
    with tenant_transaction(c.clinic.ctx):
        referral_journey.on_issued(made["id"])
        referral_journey.on_issued(made["id"])
    pump()
    with tenant_transaction(c.clinic.ctx):
        rows = workflow.list_instances(organization_ref=c.clinic.org)
    assert len([r for r in rows if r["subject_ref"] == made["id"]]) == 1


# ------------------------------------------------------------------ seen is not accepted
def test_staff_marking_it_received_moves_the_journey_but_does_not_accept_care(c):
    made = sent(c)
    assert acknowledge(c, made["id"]).status_code == 200
    pump()
    assert journey(c, made["id"])["state"] == "acknowledged"
    with tenant_transaction(c.clinic.ctx):
        assert Referral.objects.get(public_id=made["id"]).state == "issued"     # still not accepted
    assert c.incoming()[0]["journey"] == "acknowledged"
    assert c.incoming()[0]["responsible"]["id"] == c.doc                # still the sender's


def test_marking_it_received_twice_is_harmless(c):
    made = sent(c)
    acknowledge(c, made["id"])
    acknowledge(c, made["id"])
    pump()
    assert journey(c, made["id"])["state"] == "acknowledged"
    assert len([s for s in steps(c, made["id"])["steps"] if s["transition"] == "acknowledge"]) == 1


# ------------------------------------------------------------------ decisions
def test_an_accepted_referral_completes_its_journey_with_a_clinicians_acknowledgment(c):
    made = sent(c)
    assert c.decide(made["id"], "accepted").status_code == 200
    pump()
    inst = journey(c, made["id"])
    assert (inst["state"], inst["status"]) == ("accepted", "completed")
    history = steps(c, made["id"])
    assert history["chain_ok"] is True
    last = history["steps"][-1]
    assert (last["transition"], last["actor_kind"]) == ("accept", "clinician")
    assert last["actor_ref"] == c.doc2
    assert last["evidence"] == [{"kind": "professional_acknowledgment", "ref": made["id"]}]


def test_a_declined_referral_ends_its_journey_as_cancelled(c):
    made = sent(c)
    assert c.decide(made["id"], "declined", "Not our speciality").status_code == 200
    pump()
    inst = journey(c, made["id"])
    assert (inst["state"], inst["status"]) == ("declined", "cancelled")


def test_asking_for_more_information_then_accepting_follows_the_referral(c):
    made = sent(c)
    assert c.decide(made["id"], "needs_info", "Please send the ECG").status_code == 200
    pump()
    assert journey(c, made["id"])["state"] == "needs_info"
    assert c.incoming()[0]["journey"] == "needs_info"
    assert c.decide(made["id"], "accepted").status_code == 200
    pump()
    assert journey(c, made["id"])["state"] == "accepted"


def test_asking_for_more_information_twice_keeps_it_waiting_for_the_sender(c):
    made = sent(c)
    c.decide(made["id"], "needs_info", "Please send the ECG")
    c.decide(made["id"], "needs_info", "And the blood results")
    pump()
    assert journey(c, made["id"])["state"] == "needs_info"


def test_a_decision_that_is_refused_does_not_move_the_journey(c):
    made = sent(c)
    for who in ("ops", "doc", "pat"):                       # not the named owner (or not a doctor)
        assert c.decide(made["id"], "accepted", subject=who).status_code == 403
    pump()
    assert journey(c, made["id"])["state"] == "sent"
    assert c.incoming()[0]["state"] == "issued"


def test_a_journey_that_has_ended_cannot_be_moved_again(c):
    made = sent(c)
    c.decide(made["id"], "declined", "Not our speciality")
    pump()
    with tenant_transaction(c.clinic.ctx):
        referral_journey.on_decided(made["id"], "accepted", c.doc2)       # a stray late event
        referral_journey.on_acknowledged(made["id"], c.desk)
    assert journey(c, made["id"])["state"] == "declined"


def test_only_a_person_may_move_a_referral_and_only_a_clinician_may_accept(c):
    made = sent(c)
    inst = journey(c, made["id"])
    with tenant_transaction(c.clinic.ctx):
        for kind in ("system", "partner"):                  # the journey refuses non-people itself
            with pytest.raises(ZoikoError) as caught:
                workflow.engine.submit_transition(
                    instance_ref=inst["public_id"], transition="acknowledge", actor_ref="someone",
                    actor_kind=kind, expected_version=inst["entity_version"])
            assert caught.value.code == ErrorCode.POLICY_DENIED
        with pytest.raises(ZoikoError):                  # accepting needs a clinician's evidence
            workflow.engine.submit_transition(
                instance_ref=inst["public_id"], transition="accept", actor_ref=c.doc2,
                actor_kind="person", expected_version=inst["entity_version"])


# ------------------------------------------------------------------ nothing invented
def test_a_referral_with_no_journey_is_left_alone_and_never_gets_one_late(c):
    made = issued(c)                                       # events not delivered yet: no journey
    with tenant_transaction(c.clinic.ctx):
        assert workflow.instances_for([made["id"]], KEY) == {}
        referral_journey.on_decided(made["id"], "accepted", c.doc2)      # nothing to move
        referral_journey.on_acknowledged(made["id"], c.desk)
        assert workflow.instances_for([made["id"]], KEY) == {}
    assert c.incoming()[0]["journey"] is None


def test_an_unknown_referral_is_ignored(c):
    with tenant_transaction(c.clinic.ctx):
        referral_journey.on_issued("doesnotexist000000000")
        referral_journey.on_decided("doesnotexist000000000", "accepted", c.doc2)


# ------------------------------------------------------------------ a stalled referral
def test_a_referral_nobody_sees_raises_an_exception_and_is_not_closed_by_it(c):
    made = sent(c)
    later = FixedClock(dt.datetime.now(UTC) + dt.timedelta(days=2))
    with tenant_transaction(c.clinic.ctx):
        assert workflow.engine.fire_due_timers(clock=later) >= 1
        found = [e for e in workflow.list_exceptions(organization_ref=c.clinic.org)
                 if e["detail"] == "acknowledge_by"]
    assert len(found) == 1
    assert journey(c, made["id"])["state"] == "sent"                  # a timer never decides
    with tenant_transaction(c.clinic.ctx):
        assert Referral.objects.get(public_id=made["id"]).state == "issued"


def test_a_referral_seen_but_not_decided_raises_a_later_exception(c):
    made = sent(c)
    acknowledge(c, made["id"])
    pump()
    later = FixedClock(dt.datetime.now(UTC) + dt.timedelta(days=5))
    with tenant_transaction(c.clinic.ctx):
        workflow.engine.fire_due_timers(clock=later)
        details = {e["detail"] for e in workflow.list_exceptions(organization_ref=c.clinic.org)}
    assert "decide_by" in details and "acknowledge_by" not in details
    assert journey(c, made["id"])["state"] == "acknowledged"


# ------------------------------------------------------------------ who can see the journey
def test_another_clinic_does_not_see_this_clinics_referral_journeys(c):
    sent(c)
    other = Clinic2()
    with tenant_transaction(other.clinic.ctx):
        assert workflow.list_instances(organization_ref=other.clinic.org) == []
