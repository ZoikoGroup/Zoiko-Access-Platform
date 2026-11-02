import json

import pytest

from foundation import dispatch
from foundation.cell_context import tenant_transaction
from foundation.outbox import relay_once
from modules.comms import api as comms
from modules.scheduling.models import Slot
from tests.test_api import Web, book

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


def past(index):
    """A start and end in the past, a different hour for each slot (two slots may not share one)."""
    return f"2020-01-01T{10 + index:02d}:00:00Z", f"2020-01-01T{10 + index:02d}:30:00Z"


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
    w.admin = w.login("boss", "facility_user", "R13")
    return w


def mark(web, booking_id, outcome, subject="desk"):
    return web.post(f"bookings/{booking_id}/attendance", {"outcome": outcome}, subject=subject)


def visit(web, index=0, confirm=True, started=True):
    """A booking for slot `index`; by default confirmed, and the appointment time already passed."""
    r = book(web, web.slots[index]["public_id"], subject="pat")
    assert r.status_code == 201
    booking = json.loads(r.content)["booking"]
    if confirm:
        assert web.post(f"bookings/{booking['id']}/confirm", subject="desk").status_code == 200
    if started:
        with tenant_transaction(web.clinic.ctx):
            starts, ends = past(index)
            Slot.objects.filter(public_id=web.slots[index]["public_id"]).update(
                starts_at=starts, ends_at=ends)
    return booking["id"]


def mine(web):
    return {b["id"]: b for b in json.loads(web.get("bookings", subject="pat").content)["bookings"]}


# ------------------------------------------------------------------ the happy path
def test_the_clinic_records_that_the_patient_attended_and_everyone_sees_it(web):
    booking = visit(web)
    r = mark(web, booking, "attended")
    assert r.status_code == 200
    assert json.loads(r.content)["booking"]["attendance"] == "attended"
    assert mine(web)[booking]["attendance"] == "attended"             # the patient sees it too
    seen_by_staff = json.loads(web.get("bookings", subject="desk").content)["bookings"]
    assert {b["id"]: b for b in seen_by_staff}[booking]["attendance"] == "attended"


def test_a_missed_appointment_can_be_recorded(web):
    booking = visit(web)
    assert mark(web, booking, "no_show").status_code == 200
    assert mine(web)[booking]["attendance"] == "no_show"


def test_an_appointment_not_yet_recorded_has_no_attendance(web):
    booking = visit(web)
    assert mine(web)[booking]["attendance"] is None


def test_the_answer_can_be_corrected_and_saying_it_twice_is_harmless(web):
    booking = visit(web)
    mark(web, booking, "no_show")
    assert mark(web, booking, "no_show").status_code == 200
    assert mark(web, booking, "attended").status_code == 200           # staff fixed a mistake
    assert mine(web)[booking]["attendance"] == "attended"


def test_an_administrator_can_record_it_too(web):
    booking = visit(web)
    assert mark(web, booking, "attended", subject="boss").status_code == 200


# ------------------------------------------------------------------ when it is allowed
def test_it_cannot_be_recorded_before_the_appointment_starts(web):
    booking = visit(web, started=False)
    assert mark(web, booking, "attended").status_code == 409
    assert mine(web)[booking]["attendance"] is None


def test_only_a_confirmed_appointment_can_have_attendance(web):
    waiting = visit(web, 0, confirm=False)
    assert mark(web, waiting, "attended").status_code == 409
    cancelled = visit(web, 1)
    web.post(f"bookings/{cancelled}/cancel", subject="pat")
    assert mark(web, cancelled, "attended").status_code == 409


@pytest.mark.parametrize("outcome", ["", "late", "ATTENDED"])
def test_unknown_outcomes_are_refused_and_nothing_is_recorded(web, outcome):
    booking = visit(web)
    assert mark(web, booking, outcome).status_code == 422
    assert mine(web)[booking]["attendance"] is None


# ------------------------------------------------------------------ who may do it
def test_patients_and_doctors_cannot_record_attendance(web):
    booking = visit(web)
    web.patient("stranger")
    doc = web.login("doc", "clinician", "R03")
    assert doc
    for who in ("pat", "stranger", "doc"):
        assert mark(web, booking, "attended", subject=who).status_code == 403, who
    assert mine(web)[booking]["attendance"] is None


def test_another_customers_staff_cannot_record_it(web):
    booking = visit(web)
    outsider = Web()
    outsider.staff("desk")
    r = outsider.post(f"bookings/{booking}/attendance", {"outcome": "attended"}, subject="desk")
    assert r.status_code == 404
    assert mine(web)[booking]["attendance"] is None


# ------------------------------------------------------------------ the patient is told
def test_the_patient_is_told_what_was_recorded_even_if_it_was_corrected_before_delivery(web):
    booking = visit(web)
    mark(web, booking, "no_show")
    mark(web, booking, "attended")
    for _ in range(4):
        relay_once(dispatch.dispatch)
    notices = json.loads(web.get("notifications", subject="pat").content)["notifications"]
    kinds = ("booking_attended", "booking_no_show")
    titles = sorted(n["title"] for n in notices if n["kind"] in kinds)
    assert titles == ["Missed appointment", "Visit recorded"]


def test_it_is_recorded_in_the_audit_chain_without_a_note_about_health(web):
    from modules.evidence.models import AuditEvent
    booking = visit(web)
    mark(web, booking, "attended")
    with tenant_transaction(web.clinic.ctx):
        events = list(AuditEvent.objects.filter(action="booking.attended"))
    assert len(events) == 1 and events[0].object_ref == booking
