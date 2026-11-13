import datetime as dt
import json
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import DatabaseError, IntegrityError, transaction

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import relay_once
from foundation.timeutil import FixedClock, utc_now
from modules.workflow import api as workflow
from modules.workflow.history import _parts, log_event
from modules.workflow.models import Kind, WorkItem, WorkItemEvent
from modules.workflow.services import close_item, open_item
from tests.test_attention import booked
from tests.test_clinician import Practice
from tests.test_messaging import reply, started
from tests.test_work_queue import refer

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    workflow.register_subscribers()


@pytest.fixture
def p():
    practice = Practice()
    practice.staff("ops2")                                          # a second receptionist
    practice.name_referral_owner()
    return practice


def deliver():
    for _ in range(4):
        relay_once(dispatch.dispatch)


def waiting(p):
    """A requested booking, and the id of the work item it opened."""
    booking = booked(p, confirm=False)
    deliver()
    items = json.loads(p.get("staff/work", subject="ops").content)["items"]
    (item,) = [i for i in items if i["subject"] == booking]
    return booking, item["id"]


def history(p, item, subject="ops"):
    r = p.get(f"staff/work/{item}/history", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def steps(p, item, subject="ops"):
    return [e["event"] for e in history(p, item, subject)["events"]]


def rows(p, item):
    with tenant_transaction(p.clinic.ctx):
        return list(WorkItemEvent.objects.select_related("item").filter(
            item__public_id=item).order_by("seq"))


# ------------------------------------------------------------------ each step is written
def test_work_that_arrives_writes_an_opened_step_by_the_system(p):
    _, item = waiting(p)
    view = history(p, item)
    assert view["chain_ok"] is True and view["status"] == "open"
    [step] = view["events"]
    assert (step["seq"], step["event"], step["text"]) == (1, "opened", "Work arrived")
    assert step["who"] == "Automatic" and step["at"]


def test_taking_and_giving_back_name_the_person(p):
    _, item = waiting(p)
    p.post(f"staff/work/{item}/claim", subject="ops")
    p.post(f"staff/work/{item}/release", subject="ops")
    p.post(f"staff/work/{item}/claim", subject="ops2")
    view = history(p, item)
    assert [e["event"] for e in view["events"]] == ["opened", "claimed", "released", "claimed"]
    who = [e["who"] for e in view["events"]]
    assert who[0] == "Automatic" and who[1] == who[2] != who[3]
    assert all(w not in ("", "Clinic staff") for w in who[1:])
    assert [e["text"] for e in view["events"]][1:3] == ["Taken", "Given back"]


def test_a_step_that_changes_nothing_writes_nothing(p):
    _, item = waiting(p)
    p.post(f"staff/work/{item}/claim", subject="ops")
    p.post(f"staff/work/{item}/claim", subject="ops")               # already mine
    assert steps(p, item) == ["opened", "claimed"]


def test_a_refused_step_writes_nothing(p):
    _, item = waiting(p)
    p.post(f"staff/work/{item}/claim", subject="ops")
    assert p.post(f"staff/work/{item}/claim", subject="ops2").status_code == 409     # held
    assert p.post(f"staff/work/{item}/release", subject="ops2").status_code == 409   # not theirs
    assert steps(p, item) == ["opened", "claimed"]


def test_finishing_the_work_closes_the_history_as_done_in_the_name_of_who_did_it(p):
    booking, item = waiting(p)
    p.post(f"staff/work/{item}/claim", subject="ops")
    p.post(f"bookings/{booking}/confirm", subject="ops2")             # a colleague finishes it
    deliver()
    view = history(p, item)
    assert [e["event"] for e in view["events"]] == ["opened", "claimed", "done"]
    assert view["status"] == "done"
    taken, finished = view["events"][1]["who"], view["events"][2]["who"]
    assert taken != finished                                          # two different people
    assert {taken, finished}.isdisjoint({"Automatic", "Clinic staff", "The patient or family"})


def test_work_that_is_no_longer_needed_is_closed_as_such(p):
    booking, item = waiting(p)
    p.post(f"bookings/{booking}/cancel", subject="pat")
    deliver()
    view = history(p, item)
    assert [e["event"] for e in view["events"]] == ["opened", "cancelled"]
    assert view["events"][-1]["text"] == "No longer needed" and view["status"] == "cancelled"
    assert view["events"][-1]["who"] == "The patient or family"        # the patient cancelled it


def test_opening_the_same_work_twice_writes_one_step_and_so_does_closing_twice(p):
    due = utc_now() + dt.timedelta(hours=5)
    with tenant_transaction(p.clinic.ctx):
        for _ in range(2):
            open_item(organization_ref=p.clinic.org, kind=Kind.ANSWER_MESSAGE,
                      subject_ref="thread-1", due_at=due)
        item = WorkItem.objects.get(subject_ref="thread-1").public_id
        assert close_item(kind=Kind.ANSWER_MESSAGE, subject_ref="thread-1") == 1
        assert close_item(kind=Kind.ANSWER_MESSAGE, subject_ref="thread-1") == 0
    assert steps(p, item) == ["opened", "done"]


def test_redelivering_an_event_does_not_repeat_a_step(p):
    _, item = waiting(p)
    deliver()
    deliver()
    assert steps(p, item) == ["opened"]


def test_each_item_has_its_own_chain_starting_at_one(p):
    _, first = waiting(p)
    _, second = waiting(p)
    p.post(f"staff/work/{first}/claim", subject="ops")
    assert [e["seq"] for e in history(p, first)["events"]] == [1, 2]
    assert [e["seq"] for e in history(p, second)["events"]] == [1]


def test_work_opened_before_history_existed_shows_an_empty_honest_history(p):
    with tenant_transaction(p.clinic.ctx):
        old = WorkItem.objects.create(
            organization_ref=p.clinic.org, kind=Kind.DECIDE_REFERRAL, subject_ref="r-1",
            due_at=utc_now())
    assert history(p, old.public_id) == {"chain_ok": True, "status": "open", "events": []}


# ------------------------------------------------------------------ who may read it
def test_the_history_holds_nothing_about_the_patient_or_the_booking(p):
    booking, item = waiting(p)
    text = json.dumps(history(p, item))
    assert p.person not in text and booking not in text and "subject" not in text


def test_a_patient_and_a_stranger_clinic_cannot_read_it(p):
    _, item = waiting(p)
    assert p.get(f"staff/work/{item}/history", subject="pat").status_code == 403
    other = Practice()
    for who in ("ops", "doc", "pat"):
        assert other.get(f"staff/work/{item}/history", subject=who).status_code in (403, 404)


def test_an_unknown_item_is_not_found_and_a_missing_login_is_refused(p):
    assert p.get("staff/work/item_missing/history", subject="ops").status_code == 404
    _, item = waiting(p)
    assert p.client.get(f"/api/v1/staff/work/{item}/history").status_code == 401


# ------------------------------------------------------------------ the chain
def test_the_steps_link_to_each_other(p):
    _, item = waiting(p)
    p.post(f"staff/work/{item}/claim", subject="ops")
    a, b = rows(p, item)
    assert a.prev_digest == GENESIS and b.prev_digest == a.record_digest
    assert a.occurred_at <= b.occurred_at and a.status_after == "open"


def test_the_check_notices_every_kind_of_tampering(p):
    _, item = waiting(p)
    p.post(f"staff/work/{item}/claim", subject="ops")
    p.post(f"staff/work/{item}/release", subject="ops")
    good = rows(p, item)
    assert verify_chain(good, _parts) == (True, None)
    later = good[0].occurred_at + dt.timedelta(seconds=1)
    for field, value in (("event_type", "done"), ("status_after", "done"),
                         ("actor_ref", "someone-else"), ("occurred_at", later)):
        edited = rows(p, item)
        setattr(edited[1], field, value)
        assert verify_chain(edited, _parts) == (False, 2), field
    assert verify_chain([good[0], good[2]], _parts) == (False, 2)           # a step removed
    assert verify_chain([good[1], good[2]], _parts) == (False, 1)           # the first removed
    assert verify_chain([good[0], good[2], good[1]], _parts) == (False, 2)  # swapped
    other = rows(p, item)
    other[0].item.public_id = "another-item"
    assert verify_chain(other, _parts) == (False, 1)                        # moved to another item


def test_the_screen_data_reports_a_broken_chain(p, monkeypatch):
    _, item = waiting(p)
    from modules.workflow import history as module
    monkeypatch.setattr(module, "verify_chain", lambda found, parts: (False, 1))
    assert history(p, item)["chain_ok"] is False


def test_the_database_refuses_to_change_or_remove_a_step(p):
    _, item = waiting(p)
    with tenant_transaction(p.clinic.ctx):
        with pytest.raises(DatabaseError), transaction.atomic():
            WorkItemEvent.objects.filter(item__public_id=item).update(event_type="done")
        with pytest.raises(DatabaseError), transaction.atomic():
            WorkItemEvent.objects.filter(item__public_id=item).delete()
    assert steps(p, item) == ["opened"]


def test_two_steps_cannot_take_the_same_place_and_a_race_is_a_conflict(p):
    _, item = waiting(p)
    first = rows(p, item)[0]
    with tenant_transaction(p.clinic.ctx), pytest.raises(IntegrityError), transaction.atomic():
        WorkItemEvent.objects.create(
            item=first.item, seq=1, event_type="claimed", status_after="open", actor_ref="x",
            occurred_at=utc_now(), prev_digest=GENESIS, record_digest="f" * 64)
    with tenant_transaction(p.clinic.ctx):
        item_row = WorkItem.objects.get(public_id=item)
        with mock.patch.object(WorkItemEvent.objects, "create", side_effect=IntegrityError):
            with pytest.raises(ZoikoError) as caught:
                log_event(item_row, "claimed", actor_ref="x")
    assert caught.value.code == ErrorCode.CONFLICT_VERSION
    assert len(rows(p, item)) == 1


def test_only_known_steps_can_be_written_and_the_clock_is_used(p):
    _, item = waiting(p)
    moment = FixedClock(dt.datetime(2030, 1, 2, 3, 4, tzinfo=dt.timezone.utc))
    with tenant_transaction(p.clinic.ctx):
        item_row = WorkItem.objects.get(public_id=item)
        with pytest.raises(ValueError):
            log_event(item_row, "teleported", actor_ref="x")
        log_event(item_row, "claimed", actor_ref="x", clock=moment)
    last = rows(p, item)[-1]
    assert last.occurred_at == moment.now() and verify_chain(rows(p, item), _parts) == (True, None)


# ------------------------------------------------------------------ the shared chain helper
def fake(seq, prev, parts):
    return SimpleNamespace(seq=seq, prev_digest=prev, record_digest=chain_digest(prev, seq, *parts),
                           parts=parts)


def test_the_digest_depends_on_the_previous_digest_the_place_and_every_part():
    base = chain_digest(GENESIS, 1, "a", "b")
    assert base == chain_digest(GENESIS, 1, "a", "b") and len(base) == 64
    assert len({base, chain_digest("1" * 64, 1, "a", "b"), chain_digest(GENESIS, 2, "a", "b"),
                chain_digest(GENESIS, 1, "x", "b"), chain_digest(GENESIS, 1, "a", "x"),
                chain_digest(GENESIS, 1, "a")}) == 6


def test_the_helper_accepts_a_good_chain_and_rejects_each_fault():
    a = fake(1, GENESIS, ["x"])
    b = fake(2, a.record_digest, ["y"])
    c = fake(3, b.record_digest, ["z"])
    parts = lambda row: row.parts  # noqa: E731
    assert verify_chain([], parts) == (True, None)
    assert verify_chain([a, b, c], parts) == (True, None)
    assert verify_chain([b, c], parts) == (False, 1)
    assert verify_chain([a, c], parts) == (False, 2)
    assert verify_chain([a, b, c][::-1], parts) == (False, 1)
    assert verify_chain([a, fake(2, "9" * 64, ["y"])], parts) == (False, 2)       # wrong link
    bad_digest = SimpleNamespace(
        seq=2, prev_digest=a.record_digest, record_digest="0" * 64, parts=["y"])
    assert verify_chain([a, bad_digest], parts) == (False, 2)
    assert verify_chain([a, SimpleNamespace(**{**vars(b), "seq": 5})], parts) == (False, 2)


def test_closing_steps_record_the_status_the_item_ended_in(p):
    booking, item = waiting(p)
    p.post(f"bookings/{booking}/confirm", subject="ops")
    deliver()
    assert [r.status_after for r in rows(p, item)] == ["open", "done"]
    other_booking, other_item = waiting(p)
    p.post(f"bookings/{other_booking}/cancel", subject="pat")
    deliver()
    assert [r.status_after for r in rows(p, other_item)] == ["open", "cancelled"]


def test_an_item_of_another_organization_is_not_found_even_in_the_same_customer(p):
    _, item = waiting(p)
    with tenant_transaction(p.clinic.ctx):
        assert workflow.item_history(item, p.clinic.org)["item_ref"] == item
        with pytest.raises(ZoikoError) as caught:
            workflow.item_history(item, "another-organization")
    assert caught.value.code == ErrorCode.NOT_FOUND


def test_reading_a_history_is_a_checked_decision_on_record(p):
    from modules.policy.models import AccessDecision
    _, item = waiting(p)

    def decisions():
        with tenant_transaction(p.clinic.ctx):
            return AccessDecision.objects.filter(
                operation="read:analytics", actor_ref=p.desk, allowed=True).count()

    before = decisions()
    history(p, item)
    assert decisions() == before + 1


def test_a_row_with_a_valid_digest_but_the_wrong_place_is_still_rejected():
    a = fake(1, GENESIS, ["x"])
    skipped = fake(5, a.record_digest, ["y"])           # its digest fits its own place
    assert verify_chain([a, skipped], lambda row: row.parts) == (False, 2)


# ------------------------------------------------------------------ who closed it in the real world
def staff_label(p, item, event_index=-1):
    return history(p, item)["events"][event_index]["who"]


def test_a_cancellation_by_clinic_staff_is_credited_to_that_login(p):
    booking, item = waiting(p)
    p.post(f"bookings/{booking}/cancel", {"reason": "doctor_unavailable"}, subject="ops2")
    deliver()
    last = history(p, item)["events"][-1]
    assert last["event"] == "cancelled"
    assert last["who"] not in ("Automatic", "Clinic staff", "The patient or family")


def test_the_patient_is_never_identified_in_a_work_history(p):
    booking, item = waiting(p)
    p.post(f"bookings/{booking}/cancel", subject="pat")
    deliver()
    text = json.dumps(history(p, item))
    assert p.person not in text and booking not in text
    with tenant_transaction(p.clinic.ctx):
        closing = WorkItemEvent.objects.filter(item__public_id=item).order_by("seq").last()
    assert closing is not None
    assert closing.actor_ref == "patient"                  # no id is stored either


def test_a_referral_decision_closes_the_work_in_the_name_of_the_decider(p):
    booked(p, confirm=True)                                           # gives the doctor a care link
    referral = refer(p)
    deliver()
    items = json.loads(p.get("staff/work", subject="ops").content)["items"]
    (item,) = [i["id"] for i in items if i["kind"] == "decide_referral"]
    p.post(f"referrals/{referral}/decide", {"decision": "accepted"}, subject="doc2")
    deliver()
    last = history(p, item)["events"][-1]
    assert last["event"] == "done"
    assert last["who"] not in ("Automatic", "Clinic staff", "The patient or family")


def test_a_staff_reply_closes_the_message_work_in_the_name_of_who_answered(p):
    thread = started(p)
    deliver()
    items = json.loads(p.get("staff/work", subject="ops").content)["items"]
    (item,) = [i["id"] for i in items if i["kind"] == "answer_message"]
    reply(p, thread, subject="ops2")
    deliver()
    last = history(p, item)["events"][-1]
    assert last["event"] == "done"
    assert last["who"] not in ("Automatic", "Clinic staff", "The patient or family")


def test_work_that_closes_by_itself_is_still_automatic(p):
    due = utc_now() + dt.timedelta(hours=5)
    with tenant_transaction(p.clinic.ctx):
        open_item(organization_ref=p.clinic.org, kind=Kind.ANSWER_MESSAGE,
                  subject_ref="thread-9", due_at=due)
        close_item(kind=Kind.ANSWER_MESSAGE, subject_ref="thread-9")
        item = WorkItem.objects.get(subject_ref="thread-9").public_id
    assert history(p, item)["events"][-1]["who"] == "Automatic"


def events_of(*types):
    from foundation.models import OutboxEvent
    return list(OutboxEvent.objects.filter(event_type__in=types).order_by("seq"))


def test_the_events_name_a_clinic_login_but_never_a_patient(p):
    booking = booked(p, confirm=False)
    p.post(f"bookings/{booking}/confirm", subject="ops")
    p.post(f"bookings/{booking}/cancel", subject="pat")
    p.post(f"bookings/{booking}/cancel", subject="ops")          # the clinic answers
    thread = started(p)
    reply(p, thread, subject="ops")
    referral = refer(p)
    p.post(f"referrals/{referral}/decide", {"decision": "accepted"}, subject="doc2")
    with tenant_transaction(p.clinic.ctx):
        sent = {e.event_type + ":" + str(e.payload.get("side", "")): e.payload
                for e in events_of("booking.requested.v1", "booking.confirmed.v1",
                                   "booking.cancelled.v1", "message.sent.v1",
                                   "referral.decided.v1")}
    assert sent["booking.confirmed.v1:clinic"]["actor_ref"] == p.desk
    assert sent["booking.requested.v1:patient"].get("actor_ref") is None     # a patient: no id
    assert sent["booking.cancelled.v1:clinic"]["actor_ref"] == p.desk
    assert sent["message.sent.v1:patient"].get("actor_ref") is None
    assert sent["message.sent.v1:staff"]["actor_ref"] == p.desk
    assert sent["referral.decided.v1:"]["actor_ref"] == p.doc2
    for payload in sent.values():
        assert p.person not in json.dumps(payload)


def test_a_clinic_login_booking_for_a_patient_is_recorded_as_clinic_side(p):
    booking = booked(p, confirm=False)
    target = p.slot_of("doc", index=1)
    p.post(f"bookings/{booking}/reschedule", {"slot": target}, subject="ops")
    with tenant_transaction(p.clinic.ctx):
        (moved,) = events_of("booking.rescheduled.v1")
    assert moved.payload["side"] == "clinic" and moved.payload["actor_ref"] == p.desk


@pytest.mark.parametrize("event_type,expected", [
    ("booking.confirmed.v1", "done"), ("booking.cancelled.v1", "cancelled")])
def test_an_older_event_naming_nobody_closes_the_work_as_automatic(p, event_type, expected):
    from foundation.outbox import emit
    booking, item = waiting(p)
    with tenant_transaction(p.clinic.ctx):                 # the format before actors existed
        emit(event_type=event_type, aggregate_type="booking", aggregate_ref=booking,
             payload={"booking_ref": booking, "slot_ref": "sl_x", "service_ref": "sv_x",
                      "status": "confirmed"})
    deliver()
    last = history(p, item)["events"][-1]
    assert last["event"] == expected and last["who"] == "Automatic"
