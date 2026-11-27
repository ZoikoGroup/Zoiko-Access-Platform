"""A waiting-list request's journey (J3.waitlist), kept beside the request: waiting, offered a time,
then booked or left. The waitlist module still decides; the journey only records and raises
exceptions when a request stalls."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import UTC, FixedClock
from modules.waitlist import services as waitlist_services
from modules.waitlist.models import WaitEntry
from modules.workflow import api as workflow
from modules.workflow import waitlist_journey
from tests.test_api import book
from tests.test_waitlist import Waiting, deliver, staff_list

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

KEY = "J3.waitlist"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def w():
    return Waiting()


def joined(w, subject="pat"):
    made = json.loads(w.join(subject).content)["entry"]["id"]
    deliver()
    return made


def journey(w, entry):
    with tenant_transaction(w.clinic.ctx):
        return workflow.instances_for([entry], KEY).get(entry)


def steps(w, entry):
    with tenant_transaction(w.clinic.ctx):
        return workflow.instance_history(journey(w, entry)["public_id"])["steps"]


def return_offers(w):
    with tenant_transaction(w.clinic.ctx):
        return waitlist_services.expire_old_offers(older_than=dt.timedelta(seconds=0))


# ------------------------------------------------------------------ the journey starts
def test_joining_the_list_starts_a_journey_at_the_clinic(w):
    entry = joined(w)
    inst = journey(w, entry)
    assert (inst["state"], inst["status"]) == ("waiting", "running")
    assert inst["organization_ref"] == w.clinic.org


def test_a_second_event_for_the_same_request_does_not_start_a_second_journey(w):
    entry = joined(w)
    with tenant_transaction(w.clinic.ctx):
        waitlist_journey.on_joined(entry)
        waitlist_journey.on_joined(entry)
        rows = workflow.list_instances(organization_ref=w.clinic.org)
    assert len([r for r in rows if r["subject_ref"] == entry]) == 1


def test_the_journey_is_listed_with_the_clinics_journeys(w):
    entry = joined(w)
    body = json.loads(w.get("staff/journeys", subject="desk").content)
    assert KEY in {d["key"] for d in body["definitions"]}
    with tenant_transaction(w.clinic.ctx):
        subjects = {i["subject_ref"] for i in workflow.list_instances(
            organization_ref=w.clinic.org)}
    assert entry in subjects


# ------------------------------------------------------------------ offers
def test_a_free_time_moves_the_longest_waiting_request_to_offered_and_no_other(w):
    first = joined(w, "pat")
    second = joined(w, "pat2")
    w.add_times(1)
    deliver()
    assert journey(w, first)["state"] == "offered"
    assert journey(w, second)["state"] == "waiting"                 # one time, one offer


def test_an_offer_is_the_products_own_step_not_a_persons(w):
    entry = joined(w)
    w.add_times(1)
    deliver()
    offer = [s for s in steps(w, entry) if s["transition"] == "offer"][0]
    assert (offer["actor_kind"], offer["actor_ref"]) == ("system", "system")


def test_an_unused_offer_goes_back_to_waiting_and_can_be_offered_again(w):
    entry = joined(w)
    w.add_times(1)
    deliver()
    assert journey(w, entry)["state"] == "offered"
    assert return_offers(w) == 1
    deliver()
    assert journey(w, entry)["state"] == "waiting"
    w.add_times(1)
    deliver()
    assert journey(w, entry)["state"] == "offered"
    names = [s["transition"] for s in steps(w, entry)]
    assert names == ["start", "offer", "return_to_waiting", "offer"]


def test_replaying_an_offer_event_changes_nothing(w):
    entry = joined(w)
    w.add_times(1)
    deliver()
    with tenant_transaction(w.clinic.ctx):
        waitlist_journey.on_offered(entry)
        waitlist_journey.on_offered(entry)
    assert journey(w, entry)["state"] == "offered"
    assert [s["transition"] for s in steps(w, entry)] == ["start", "offer"]     # recorded once


# ------------------------------------------------------------------ how it ends
def test_booking_the_service_completes_the_journey_as_the_patients_own_step(w):
    entry = joined(w)
    w.add_times(1)
    slot = w.free_slots()[0]["id"]
    assert book(w, slot, subject="pat").status_code == 201
    deliver()
    inst = journey(w, entry)
    assert (inst["state"], inst["status"]) == ("booked", "completed")
    last = steps(w, entry)[-1]
    assert (last["transition"], last["actor_kind"]) == ("book", "person")


def test_leaving_the_list_ends_the_journey_and_records_who_left(w):
    entry = joined(w)
    assert w.post(f"waitlist/{entry}/leave", subject="pat").status_code == 200
    deliver()
    inst = journey(w, entry)
    assert (inst["state"], inst["status"]) == ("left", "cancelled")
    last = steps(w, entry)[-1]
    assert (last["transition"], last["actor_kind"]) == ("leave", "person")
    assert last["actor_ref"] != "system"


def test_staff_removing_someone_is_recorded_as_a_persons_step(w):
    entry = joined(w)
    assert w.post(f"waitlist/{entry}/leave", subject="desk").status_code == 200
    deliver()
    assert journey(w, entry)["state"] == "left"
    assert steps(w, entry)[-1]["actor_kind"] == "person"


def test_leaving_twice_adds_nothing(w):
    entry = joined(w)
    w.post(f"waitlist/{entry}/leave", subject="pat")
    w.post(f"waitlist/{entry}/leave", subject="pat")
    deliver()
    assert len([s for s in steps(w, entry) if s["transition"] == "leave"]) == 1


def test_joining_again_after_leaving_starts_a_fresh_journey(w):
    first = joined(w)
    w.post(f"waitlist/{first}/leave", subject="pat")
    deliver()
    second = joined(w)
    assert second != first
    assert journey(w, first)["status"] == "cancelled"
    assert journey(w, second)["status"] == "running"


def test_an_ended_journey_cannot_be_moved_again(w):
    entry = joined(w)
    w.post(f"waitlist/{entry}/leave", subject="pat")
    deliver()
    with tenant_transaction(w.clinic.ctx):                       # stray late events
        waitlist_journey.on_offered(entry)
        waitlist_journey.on_booked(entry)
        waitlist_journey.on_returned(entry)
    assert journey(w, entry)["state"] == "left"


def test_only_a_person_may_book_or_leave_and_the_journey_itself_refuses_the_system(w):
    entry = joined(w)
    inst = journey(w, entry)
    with tenant_transaction(w.clinic.ctx):
        for transition in ("book", "leave"):
            for kind in ("system", "partner"):
                with pytest.raises(ZoikoError) as caught:
                    workflow.engine.submit_transition(
                        instance_ref=inst["public_id"], transition=transition,
                        actor_ref="someone", actor_kind=kind,
                        expected_version=inst["entity_version"])
                assert caught.value.code == ErrorCode.POLICY_DENIED
    assert journey(w, entry)["state"] == "waiting"


# ------------------------------------------------------------------ nothing invented
def test_a_request_with_no_journey_is_left_alone_and_never_gets_one_late(w):
    made = json.loads(w.join().content)["entry"]["id"]           # events not delivered yet
    with tenant_transaction(w.clinic.ctx):
        assert workflow.instances_for([made], KEY) == {}
        waitlist_journey.on_offered(made)
        waitlist_journey.on_booked(made)
        waitlist_journey.on_left(made, "someone")
        assert workflow.instances_for([made], KEY) == {}
    assert staff_list(w)[0]["journey"] is None


def test_an_unknown_request_is_ignored(w):
    with tenant_transaction(w.clinic.ctx):
        waitlist_journey.on_joined("doesnotexist000000000")
        waitlist_journey.on_booked("doesnotexist000000000")
        waitlist_journey.on_left("doesnotexist000000000", "someone")


def test_the_journey_does_not_change_what_the_waiting_list_does(w):
    entry = joined(w)
    w.add_times(1)
    deliver()
    assert w.entries("pat")[0]["state"] == "offered"             # the waitlist's own rules
    with tenant_transaction(w.clinic.ctx):
        assert WaitEntry.objects.get(public_id=entry).state == "offered"


# ------------------------------------------------------------------ the staff screen
def test_staff_see_each_requests_journey_step(w):
    first = joined(w, "pat")
    joined(w, "pat2")
    w.add_times(1)
    deliver()
    rows = {r["id"]: r["journey"] for r in staff_list(w)}
    assert rows[first] == "offered"
    assert sorted(rows.values()) == ["offered", "waiting"]


# ------------------------------------------------------------------ a stalled request
def test_a_request_waiting_far_too_long_raises_an_exception_and_is_not_closed_by_it(w):
    entry = joined(w)
    later = FixedClock(dt.datetime.now(UTC) + dt.timedelta(days=15))
    with tenant_transaction(w.clinic.ctx):
        assert workflow.engine.fire_due_timers(clock=later) >= 1
        found = [e for e in workflow.list_exceptions(organization_ref=w.clinic.org)
                 if e["detail"] == "wait_by"]
        assert len(found) == 1
        assert WaitEntry.objects.get(public_id=entry).state == "waiting"
    assert journey(w, entry)["state"] == "waiting"               # a timer never offers or closes


def test_an_offer_nobody_answers_raises_an_exception_without_changing_the_request(w):
    entry = joined(w)
    w.add_times(1)
    deliver()
    later = FixedClock(dt.datetime.now(UTC) + dt.timedelta(days=4))
    with tenant_transaction(w.clinic.ctx):
        workflow.engine.fire_due_timers(clock=later)
        details = {e["detail"] for e in workflow.list_exceptions(organization_ref=w.clinic.org)}
        assert WaitEntry.objects.get(public_id=entry).state == "offered"
    assert "offer_by" in details and "wait_by" not in details


# ------------------------------------------------------------------ who can see it
def test_another_customer_does_not_see_this_clinics_waiting_journeys(w):
    joined(w)
    other = Waiting()
    with tenant_transaction(other.clinic.ctx):
        assert workflow.list_instances(organization_ref=other.clinic.org) == []
