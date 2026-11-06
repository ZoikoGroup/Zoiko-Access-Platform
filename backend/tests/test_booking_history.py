import datetime as dt
import json

import pytest
from django.db import DatabaseError, IntegrityError, transaction

from foundation.cell_context import tenant_transaction
from foundation.timeutil import FixedClock, utc_now
from modules.scheduling import services as scheduling_services
from modules.scheduling.history import GENESIS, _verify, log_event
from modules.scheduling.models import Booking, BookingEvent, Slot
from tests.test_api import book
from tests.test_clinician import Practice
from tests.test_reports import in_the_past

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.REMINDER_LEAD_HOURS = 24


@pytest.fixture
def p():
    practice = Practice()
    practice.add_times(practice.doc, count=6)
    return practice


def booked(p, confirm=False):
    slot = p.slot_of("doc")
    r = book(p, slot, subject="pat")
    assert r.status_code == 201, r.content
    booking = json.loads(r.content)["booking"]["id"]
    if confirm:
        assert p.post(f"bookings/{booking}/confirm", subject="ops").status_code == 200
    return booking, slot


def history(p, booking, subject="pat"):
    r = p.get(f"bookings/{booking}/history", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def steps(p, booking, subject="pat"):
    return [(e["event"], e["from"], e["to"]) for e in history(p, booking, subject)["events"]]


def rows(p, booking):
    with tenant_transaction(p.clinic.ctx):
        return list(BookingEvent.objects.filter(booking__public_id=booking).order_by("seq"))


# ------------------------------------------------------------------ each step is written
def test_requesting_writes_the_first_step_for_the_patient_and_the_staff(p):
    booking, _ = booked(p)
    mine = history(p, booking)
    assert mine["chain_ok"] is True
    [event] = mine["events"]
    assert (event["seq"], event["event"]) == (1, "requested")
    assert (event["from"], event["to"]) == ("", "requested")
    assert event["text"] == "Requested" and event["who"] == "You or your family" and event["at"]
    assert history(p, booking, "ops")["events"][0]["who"] == "The patient or family"


def test_confirming_cancelling_and_the_order_of_a_full_story(p):
    booking, _ = booked(p)
    p.post(f"bookings/{booking}/confirm", subject="ops")
    p.post(f"bookings/{booking}/cancel", {"reason": "schedule_conflict"}, subject="pat")
    assert steps(p, booking) == [
        ("requested", "", "requested"), ("confirmed", "requested", "confirmed"),
        ("cancelled", "confirmed", "cancelled")]
    last = history(p, booking)["events"][-1]
    assert last["reason"] == "schedule_conflict" and last["who"] == "You or your family"
    assert [e["seq"] for e in history(p, booking)["events"]] == [1, 2, 3]


def test_a_cancellation_by_the_clinic_is_marked_as_the_clinic(p):
    booking, _ = booked(p)
    p.post(f"bookings/{booking}/cancel", {"reason": "doctor_unavailable"}, subject="ops")
    mine, theirs = history(p, booking)["events"][-1], history(p, booking, "ops")["events"][-1]
    assert mine["who"] == "The clinic" and mine["reason"] == "doctor_unavailable"
    assert theirs["who"] not in ("The clinic", "You or your family", "The patient or family")


def test_cancelling_a_waiting_request_records_where_it_came_from(p):
    booking, _ = booked(p)
    p.post(f"bookings/{booking}/cancel", subject="pat")
    assert steps(p, booking)[-1] == ("cancelled", "requested", "cancelled")
    assert history(p, booking)["events"][-1]["reason"] == ""


def test_repeating_a_step_that_changes_nothing_adds_nothing(p):
    booking, _ = booked(p)
    p.post(f"bookings/{booking}/confirm", subject="ops")
    p.post(f"bookings/{booking}/confirm", subject="ops")
    p.post(f"bookings/{booking}/cancel", subject="pat")
    p.post(f"bookings/{booking}/cancel", {"reason": "other"}, subject="pat")
    assert [s[0] for s in steps(p, booking)] == ["requested", "confirmed", "cancelled"]


def test_a_refused_step_writes_nothing(p):
    booking, slot = booked(p)
    assert book(p, slot, subject="pat").status_code == 409          # the time is taken
    wrong_list = {"reason": "clinic_unavailable"}        # a clinic reason, given by a patient
    p.post(f"bookings/{booking}/cancel", wrong_list, subject="pat")
    assert p.post(f"bookings/{booking}/confirm", subject="pat").status_code == 403
    assert [s[0] for s in steps(p, booking)] == ["requested"]


def test_moving_an_appointment_writes_a_step_on_both_bookings(p):
    old, _ = booked(p, confirm=True)
    target = p.slot_of("doc", index=1)
    r = p.post(f"bookings/{old}/reschedule", {"slot": target}, subject="pat")
    assert r.status_code in (200, 201), r.content
    new = json.loads(r.content)["booking"]["id"]
    assert steps(p, old) == [
        ("requested", "", "requested"), ("confirmed", "requested", "confirmed"),
        ("moved_away", "confirmed", "cancelled")]
    assert steps(p, new) == [("moved_here", "", "requested")]
    assert history(p, new)["events"][0]["text"] == "Booked as the new time"
    assert rows(p, old)[-1].related_ref == new and rows(p, new)[0].related_ref == old


def test_attendance_and_its_correction_are_both_kept(p):
    booking, slot = booked(p, confirm=True)
    in_the_past(p, slot, days_ago=2)
    p.post(f"bookings/{booking}/attendance", {"outcome": "attended"}, subject="ops")
    p.post(f"bookings/{booking}/attendance", {"outcome": "attended"}, subject="ops")   # no change
    p.post(f"bookings/{booking}/attendance", {"outcome": "no_show"}, subject="ops")
    assert steps(p, booking)[2:] == [("attended", "confirmed", "confirmed"),
                                     ("no_show", "confirmed", "confirmed")]
    assert history(p, booking)["events"][-1]["text"] == "Did not attend"


def test_a_reminder_is_one_automatic_step_even_if_the_job_runs_twice(p):
    booking, slot = booked(p, confirm=True)
    soon = utc_now() + dt.timedelta(hours=3)
    with tenant_transaction(p.clinic.ctx):
        Slot.objects.filter(public_id=slot).update(
            starts_at=soon, ends_at=soon + dt.timedelta(minutes=30))
    for _ in range(2):
        with tenant_transaction(p.clinic.ctx):
            scheduling_services.send_due_reminders()
    reminders = [e for e in history(p, booking)["events"] if e["event"] == "reminder_sent"]
    assert len(reminders) == 1 and reminders[0]["who"] == "Automatic"
    assert history(p, booking, "ops")["events"][-1]["who"] == "Automatic"


# ------------------------------------------------------------------ who sees what
def test_the_patient_never_sees_which_staff_member_acted(p):
    booking, _ = booked(p)
    p.post(f"bookings/{booking}/confirm", subject="ops")
    seen = json.dumps(history(p, booking))
    assert p.desk not in seen and p.doc not in seen and "actor" not in seen
    assert "ops" not in {e["who"] for e in history(p, booking)["events"]}


def test_staff_see_which_login_confirmed_but_never_a_patient_name(p):
    p.post(f"people/{p.person}/profile", {"name": "Asha Rao"}, subject="pat")
    booking, _ = booked(p)
    p.post(f"bookings/{booking}/confirm", subject="ops")
    view = history(p, booking, "ops")
    assert "Asha" not in json.dumps(view) and p.person not in json.dumps(view)
    assert view["events"][1]["who"] not in ("The clinic", "Automatic")


def test_the_doctor_of_a_confirmed_visit_may_read_it_but_a_stranger_may_not(p):
    booking, _ = booked(p, confirm=True)
    assert p.get(f"bookings/{booking}/history", subject="doc").status_code == 200
    other = Practice()
    for who in ("pat", "ops", "doc"):
        assert other.get(f"bookings/{booking}/history", subject=who).status_code in (403, 404)


def test_an_unknown_booking_is_not_found(p):
    assert p.get("bookings/booking_missing/history", subject="pat").status_code == 404


def test_a_missing_login_is_refused(p):
    booking, _ = booked(p)
    assert p.client.get(f"/api/v1/bookings/{booking}/history").status_code == 401


def test_a_booking_made_before_history_existed_shows_an_empty_honest_history(p):
    slot = p.slot_of("doc")
    with tenant_transaction(p.clinic.ctx):
        old = Booking.objects.create(
            slot=Slot.objects.get(public_id=slot), person_ref=p.person, booked_by_ref=p.doc)
    view = history(p, old.public_id)
    assert view == {"chain_ok": True, "events": []}


def test_each_booking_has_its_own_chain_starting_at_one(p):
    first, _ = booked(p)
    second, _ = booked(p)
    p.post(f"bookings/{first}/confirm", subject="ops")
    assert [e["seq"] for e in history(p, first)["events"]] == [1, 2]
    assert [e["seq"] for e in history(p, second)["events"]] == [1]


# ------------------------------------------------------------------ the chain
def test_the_chain_links_each_step_to_the_one_before(p):
    booking, _ = booked(p, confirm=True)
    a, b = rows(p, booking)
    assert a.prev_digest == GENESIS and b.prev_digest == a.record_digest
    assert len(a.record_digest) == 64 and a.record_digest != b.record_digest
    assert a.occurred_at <= b.occurred_at


def test_the_check_notices_a_changed_step_a_missing_step_and_a_swapped_order(p):
    booking, _ = booked(p, confirm=True)
    p.post(f"bookings/{booking}/cancel", subject="pat")
    good = rows(p, booking)
    assert _verify(booking, good) == (True, None)
    assert _verify(booking, []) == (True, None)
    changed = rows(p, booking)
    changed[1].state_to = "requested"                               # a step rewritten
    assert _verify(booking, changed) == (False, 2)
    later = good[0].occurred_at + dt.timedelta(seconds=1)
    for field, value in (("actor_ref", "someone-else"), ("reason_code", "other"),
                         ("source", "system"), ("event_type", "cancelled"),
                         ("related_ref", "x"), ("state_from", "confirmed"),
                         ("state_to", "cancelled"), ("occurred_at", later)):
        edited = rows(p, booking)
        setattr(edited[0], field, value)
        assert _verify(booking, edited) == (False, 1), field
    assert _verify(booking, [good[0], good[2]]) == (False, 2)       # a step removed
    assert _verify(booking, [good[1], good[2]]) == (False, 1)       # the first step removed
    assert _verify(booking, [good[0], good[2], good[1]]) == (False, 2)    # swapped
    assert _verify("another-booking", good) == (False, 1)           # moved to another booking


def test_the_screen_reports_a_broken_chain(p, monkeypatch):
    booking, _ = booked(p)
    from modules.scheduling import history as module
    monkeypatch.setattr(module, "_verify", lambda ref, found: (False, 1))
    assert history(p, booking)["chain_ok"] is False


def test_the_database_refuses_to_change_or_remove_a_step(p):
    booking, _ = booked(p)
    with tenant_transaction(p.clinic.ctx):
        with pytest.raises(DatabaseError), transaction.atomic():
            BookingEvent.objects.filter(booking__public_id=booking).update(event_type="cancelled")
        with pytest.raises(DatabaseError), transaction.atomic():
            BookingEvent.objects.filter(booking__public_id=booking).delete()
    assert steps(p, booking) == [("requested", "", "requested")]


def test_two_steps_cannot_take_the_same_place_in_a_chain(p):
    booking, _ = booked(p)
    first = rows(p, booking)[0]
    with tenant_transaction(p.clinic.ctx), pytest.raises(IntegrityError), transaction.atomic():
        BookingEvent.objects.create(
            booking=first.booking, seq=1, event_type="confirmed", state_to="confirmed",
            source="clinic", actor_ref="x", occurred_at=utc_now(), prev_digest=GENESIS,
            record_digest="f" * 64)


def test_only_known_steps_and_sources_can_be_written(p):
    booking, _ = booked(p)
    with tenant_transaction(p.clinic.ctx):
        row = Booking.objects.get(public_id=booking)
        with pytest.raises(ValueError):
            log_event(row, "teleported", state_from="", state_to="x", source="clinic",
                      actor_ref="a")
        with pytest.raises(ValueError):
            log_event(row, "confirmed", state_from="", state_to="x", source="nobody", actor_ref="a")
    assert len(rows(p, booking)) == 1


def test_a_step_takes_the_time_of_the_clock_it_is_given(p):
    booking, _ = booked(p)
    moment = FixedClock(dt.datetime(2030, 1, 2, 3, 4, tzinfo=dt.timezone.utc))
    with tenant_transaction(p.clinic.ctx):
        row = Booking.objects.get(public_id=booking)
        log_event(row, "reminder_sent", state_from="requested", state_to="requested",
                  source="system", actor_ref="system", clock=moment)
    assert rows(p, booking)[-1].occurred_at == moment.now()
    assert _verify(booking, rows(p, booking)) == (True, None)


def test_a_race_for_the_same_place_is_reported_as_a_conflict_and_writes_nothing(p):
    from unittest import mock

    from foundation.errors import ErrorCode, ZoikoError
    booking, _ = booked(p)
    with tenant_transaction(p.clinic.ctx):
        row = Booking.objects.get(public_id=booking)
        with mock.patch.object(BookingEvent.objects, "create", side_effect=IntegrityError):
            with pytest.raises(ZoikoError) as caught:
                log_event(row, "confirmed", state_from="requested", state_to="confirmed",
                          source="clinic", actor_ref="a")
    assert caught.value.code == ErrorCode.CONFLICT_VERSION
    assert len(rows(p, booking)) == 1


def test_a_doctor_with_no_link_to_the_patient_may_not_read_it(p):
    booking, _ = booked(p, confirm=True)                            # linked to "doc" only
    assert p.get(f"bookings/{booking}/history", subject="doc2").status_code == 403
