"""A patient's cancel or move of a CONFIRMED appointment only asks; the original stays until the
clinic answers (GHE-14 WF-13, T-26, T-27). DEV-108."""
import json

import pytest

from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.add_times(practice.doc, count=6)
    return practice


def booked(p, index=0, confirm=True):
    r = book(p, p.slot_of("doc", index), subject="pat")
    assert r.status_code == 201, r.content
    booking = json.loads(r.content)["booking"]["id"]
    if confirm:
        assert p.post(f"bookings/{booking}/confirm", subject="ops").status_code == 200
    return booking


def row(p, booking, subject="ops"):
    rows = json.loads(p.get("bookings", subject=subject).content)["bookings"]
    return next(r for r in rows if r["id"] == booking)


def move(p, booking, index, subject="pat"):
    r = p.post(f"bookings/{booking}/reschedule", {"slot": p.slot_of("doc", index)}, subject=subject)
    assert r.status_code in (200, 201), r.content
    return json.loads(r.content)["booking"]["id"]


def open_times(p):
    return len(p.open_slots())


# ------------------------------------------------------------------ cancelling
def test_a_patients_cancel_of_a_confirmed_appointment_only_asks(p):
    booking = booked(p)
    free_before = open_times(p)
    r = p.post(f"bookings/{booking}/cancel", {"reason": "schedule_conflict"}, subject="pat")
    assert r.status_code == 200
    seen = row(p, booking)
    assert seen["status"] == "confirmed" and seen["pending_change"] == "cancel"
    assert open_times(p) == free_before                       # the time is NOT released yet
    assert [a["id"] for a in p.schedule()] == [booking]       # the doctor still has it


def test_the_clinic_answering_completes_the_cancel_and_keeps_the_patients_reason(p):
    booking = booked(p)
    free_before = open_times(p)
    p.post(f"bookings/{booking}/cancel", {"reason": "schedule_conflict"}, subject="pat")
    assert p.post(f"bookings/{booking}/cancel", subject="ops").status_code == 200
    seen = row(p, booking)
    assert seen["status"] == "cancelled" and seen["pending_change"] is None
    assert seen["cancel_reason"] == "schedule_conflict"
    assert open_times(p) == free_before + 1 and p.schedule() == []


def test_asking_to_cancel_twice_is_harmless(p):
    booking = booked(p)
    for _ in range(2):
        assert p.post(f"bookings/{booking}/cancel", subject="pat").status_code == 200
    assert row(p, booking)["pending_change"] == "cancel"


def test_cancelling_a_waiting_request_is_still_immediate(p):
    booking = booked(p, confirm=False)
    p.post(f"bookings/{booking}/cancel", subject="pat")
    assert row(p, booking)["status"] == "cancelled"


def test_the_clinic_cancelling_by_itself_is_immediate(p):
    booking = booked(p)
    p.post(f"bookings/{booking}/cancel", {"reason": "doctor_unavailable"}, subject="ops")
    assert row(p, booking)["status"] == "cancelled"


# ------------------------------------------------------------------ moving
def test_a_patients_move_keeps_the_original_until_the_clinic_confirms_the_new_time(p):
    old = booked(p, 0)
    new = move(p, old, 1)
    assert row(p, old)["status"] == "confirmed" and row(p, old)["pending_change"] == "rebook"
    assert row(p, new)["status"] == "requested" and row(p, new)["rescheduled_from"] == old
    assert p.post(f"bookings/{new}/confirm", subject="ops").status_code == 200
    assert row(p, old)["status"] == "cancelled" and row(p, old)["pending_change"] is None
    assert row(p, new)["status"] == "confirmed"
    assert [a["id"] for a in p.schedule()] == [new]


def test_when_the_new_request_is_cancelled_the_original_stays(p):
    old = booked(p, 0)
    new = move(p, old, 1)
    p.post(f"bookings/{new}/cancel", subject="ops")            # the clinic turns it down
    assert row(p, new)["status"] == "cancelled"
    assert row(p, old)["status"] == "confirmed" and row(p, old)["pending_change"] is None


def test_when_the_patient_withdraws_the_new_time_the_original_stays(p):
    old = booked(p, 0)
    new = move(p, old, 1)
    p.post(f"bookings/{new}/cancel", subject="pat")
    assert row(p, old)["status"] == "confirmed" and row(p, old)["pending_change"] is None
    assert [a["id"] for a in p.schedule()] == [old]


def test_only_one_change_can_wait_at_a_time(p):
    old = booked(p, 0)
    move(p, old, 1)
    again = p.post(f"bookings/{old}/reschedule", {"slot": p.slot_of("doc", 1)}, subject="pat")
    assert again.status_code == 409
    assert p.post(f"bookings/{old}/cancel", subject="pat").status_code == 409


def test_moving_the_waiting_request_again_still_replaces_the_original(p):
    old = booked(p, 0)
    first_try = move(p, old, 1)
    second_try = move(p, first_try, 2)             # a request, not confirmed yet: replaced
    assert row(p, first_try)["status"] == "cancelled"
    assert row(p, second_try)["rescheduled_from"] == old
    assert row(p, old)["pending_change"] == "rebook"
    p.post(f"bookings/{second_try}/confirm", subject="ops")
    assert row(p, old)["status"] == "cancelled"
    assert [a["id"] for a in p.schedule()] == [second_try]


def test_the_clinics_own_move_of_a_confirmed_appointment_is_still_one_step(p):
    old = booked(p, 0)
    new = move(p, old, 1, subject="ops")
    assert row(p, old)["status"] == "cancelled" and row(p, new)["status"] == "confirmed"
