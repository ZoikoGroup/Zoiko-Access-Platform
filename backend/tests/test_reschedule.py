import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.outbox import relay_once
from modules.comms import api as comms
from modules.scheduling.models import Slot
from tests.test_api import Web, book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()


@pytest.fixture
def web():
    w = Web()
    w.patient("pat")
    w.staff("desk")
    return w


def move(web, booking_id, slot, subject="pat"):
    return web.post(f"bookings/{booking_id}/reschedule", {"slot": slot}, subject=subject)


def bookings(web, subject="pat"):
    r = web.get("bookings", subject=subject)
    assert r.status_code == 200
    return {b["id"]: b for b in json.loads(r.content)["bookings"]}


def made(web, slot_index=0, subject="pat"):
    r = book(web, web.slots[slot_index]["public_id"], subject=subject)
    assert r.status_code == 201
    return json.loads(r.content)["booking"]


# ------------------------------------------------------------------ the happy path
def test_a_patient_moves_an_appointment_and_the_old_time_is_free_again(web):
    first = made(web, 0)
    r = move(web, first["id"], web.slots[1]["public_id"])
    assert r.status_code == 200
    new = json.loads(r.content)["booking"]
    assert new["status"] == "requested" and new["rescheduled_from"] == first["id"]
    assert new["id"] != first["id"] and new["starts_at"] == web.slots[1]["starts_at"]
    seen = bookings(web)
    assert seen[first["id"]]["status"] == "cancelled" and seen[new["id"]]["status"] == "requested"
    # the old time can be booked by someone else, the new one cannot
    assert book(web, web.slots[0]["public_id"], subject="pat").status_code == 201
    other = web.patient("pat2")
    assert other and book(web, web.slots[1]["public_id"], subject="pat2").status_code == 409


def test_a_patients_move_of_a_confirmed_appointment_needs_the_clinic_to_confirm_again(web):
    first = made(web, 0)
    assert web.post(f"bookings/{first['id']}/confirm", subject="desk").status_code == 200
    new = json.loads(move(web, first["id"], web.slots[1]["public_id"]).content)["booking"]
    assert new["status"] == "requested"


def test_clinic_staff_can_move_a_confirmed_appointment_and_it_stays_confirmed(web):
    first = made(web, 0)
    web.post(f"bookings/{first['id']}/confirm", subject="desk")
    r = move(web, first["id"], web.slots[1]["public_id"], subject="desk")
    assert r.status_code == 200
    new = json.loads(r.content)["booking"]
    assert new["status"] == "confirmed" and new["rescheduled_from"] == first["id"]


def test_staff_moving_a_requested_appointment_does_not_confirm_it(web):
    first = made(web, 0)
    new = json.loads(move(web, first["id"], web.slots[1]["public_id"], subject="desk").content)
    assert new["booking"]["status"] == "requested"


# ------------------------------------------------------------------ nothing half-done
def test_if_the_new_time_was_just_taken_nothing_changes(web):
    web.patient("pat2")
    first = made(web, 0)
    made(web, 1, subject="pat2")                       # someone else holds the time I want
    r = move(web, first["id"], web.slots[1]["public_id"])
    assert r.status_code == 409
    assert bookings(web)[first["id"]]["status"] == "requested"       # still mine


@pytest.mark.parametrize("why", ["same", "unknown", "withdrawn", "past", "other_service"])
def test_a_time_that_does_not_fit_is_refused_and_the_booking_is_left_alone(web, why):
    first = made(web, 0)
    target = web.slots[1]["public_id"]
    if why == "same":
        target = web.slots[0]["public_id"]
    elif why == "unknown":
        target = "nosuchslot00000000000"
    elif why == "withdrawn":
        with tenant_transaction(web.clinic.ctx):
            Slot.objects.filter(public_id=target).update(status="withdrawn")
    elif why == "past":
        with tenant_transaction(web.clinic.ctx):
            Slot.objects.filter(public_id=target).update(starts_at="2020-01-01T00:00:00Z")
    else:
        other = web.post("services", {
            "organization": web.clinic.org, "name": "Dermatology", "category": "specialist",
            "duration_minutes": 20}, subject="desk")
        service = json.loads(other.content)["service"]["id"]
        web.post(f"services/{service}/slots", {
            "location": web.clinic.location, "first_local": "2027-09-01T09:00:00", "count": 1},
            subject="desk")
        target = json.loads(web.get(f"services/{service}/slots", subject="pat").content)[
            "slots"][0]["id"]
    r = move(web, first["id"], target)
    assert r.status_code in (404, 422), (why, r.status_code)
    assert bookings(web)[first["id"]]["status"] == "requested"


def test_a_cancelled_or_past_booking_cannot_be_moved(web):
    first = made(web, 0)
    web.post(f"bookings/{first['id']}/cancel", subject="pat")
    assert move(web, first["id"], web.slots[1]["public_id"]).status_code == 409
    second = made(web, 2)
    with tenant_transaction(web.clinic.ctx):
        Slot.objects.filter(public_id=web.slots[2]["public_id"]).update(
            starts_at="2020-01-01T00:00:00Z", ends_at="2020-01-01T00:30:00Z")
    assert move(web, second["id"], web.slots[1]["public_id"]).status_code == 409


def test_moving_twice_in_a_row_works_and_only_the_last_stays_active(web):
    first = made(web, 0)
    second = json.loads(move(web, first["id"], web.slots[1]["public_id"]).content)["booking"]
    third = json.loads(move(web, second["id"], web.slots[2]["public_id"]).content)["booking"]
    seen = bookings(web)
    assert [b["status"] for b in seen.values()].count("cancelled") == 2
    assert seen[third["id"]]["status"] == "requested" and third["rescheduled_from"] == second["id"]
    assert move(web, first["id"], web.slots[2]["public_id"]).status_code == 409   # old one


# ------------------------------------------------------------------ who may do it
def test_only_the_patient_or_clinic_staff_can_move_it(web):
    first = made(web, 0)
    web.patient("stranger")
    assert move(web, first["id"], web.slots[1]["public_id"], subject="stranger").status_code == 403
    assert bookings(web)[first["id"]]["status"] == "requested"


def test_another_customers_staff_cannot_move_it(web):
    first = made(web, 0)
    outsider = Web()
    outsider.staff("desk")
    r = outsider.post(f"bookings/{first['id']}/reschedule", {"slot": web.slots[1]["public_id"]},
                      subject="desk")
    assert r.status_code == 404
    assert bookings(web)[first["id"]]["status"] == "requested"


def test_a_doctor_cannot_move_a_booking():
    practice = Practice()
    practice.add_times(practice.doc)
    slot = practice.slot_of("doc", 0)
    first = json.loads(book(practice, slot, subject="pat").content)["booking"]
    other = practice.slot_of("doc", 0)                   # the one time still free
    r = practice.post(f"bookings/{first['id']}/reschedule", {"slot": other}, subject="doc")
    assert r.status_code == 403


# ------------------------------------------------------------------ the doctor and the patient
def test_the_doctor_sees_the_new_time_only_once_the_clinic_confirms_it():
    practice = Practice()
    practice.add_times(practice.doc)
    first = json.loads(book(practice, practice.slot_of("doc", 0), subject="pat").content)["booking"]
    practice.post(f"bookings/{first['id']}/confirm", subject="ops")
    assert len(practice.schedule()) == 1
    moved = move(practice, first["id"], practice.slot_of("doc", 0))     # the time still free
    assert moved.status_code == 200
    new = json.loads(moved.content)["booking"]
    assert practice.schedule() == []                       # moved: waiting for the clinic
    practice.post(f"bookings/{new['id']}/confirm", subject="ops")
    assert [a["id"] for a in practice.schedule()] == [new["id"]]


def test_the_patient_is_told_their_appointment_was_moved(web):
    first = made(web, 0)
    move(web, first["id"], web.slots[1]["public_id"])
    for _ in range(3):
        relay_once(dispatch.dispatch)
    notices = json.loads(web.get("notifications", subject="pat").content)["notifications"]
    moved = [n for n in notices if n["kind"] == "booking_rescheduled"]
    assert len(moved) == 1 and moved[0]["title"] == "Appointment moved"
    assert "clinic will confirm" in moved[0]["body"]
    assert not [n for n in notices if n["kind"] == "booking_cancelled"]   # no second message
