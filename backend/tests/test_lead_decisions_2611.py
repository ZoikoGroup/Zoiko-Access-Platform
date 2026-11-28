"""What the decisions of 26 November 2026 changed (DEV-390): a patient may take back a request to
cancel a confirmed appointment before the clinic answers, and Zoiko's own staff must use two-step
sign-in before any release can go live. Mostly refusals."""
import json

import pytest

from tests import test_readiness
from tests.test_api import book
from tests.test_clinician import Practice
from tests.test_readiness import call, release, require_zoiko_two_step

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

world = test_readiness.world


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def confirmed(p):
    slot = p.open_slots()[0]["id"]
    made = book(p, slot)
    assert made.status_code == 201, made.content
    booking = json.loads(made.content)["booking"]["id"]
    assert p.post(f"bookings/{booking}/confirm", {}, subject="ops").status_code == 200
    return booking


def state(p, booking):
    rows = json.loads(p.get("bookings", subject="pat").content)["bookings"]
    return next(b for b in rows if b["id"] == booking)


# ------------------------------------------------------------------ taking back a cancel request
def test_the_patient_takes_back_their_cancel_request_and_the_appointment_stays(p):
    booking = confirmed(p)
    assert p.post(f"bookings/{booking}/cancel", {}, subject="pat").status_code == 200
    assert state(p, booking)["pending_change"] == "cancel"
    r = p.post(f"bookings/{booking}/keep", {}, subject="pat")
    assert r.status_code == 200, r.content
    now = state(p, booking)
    assert now["status"] == "confirmed" and not now["pending_change"]
    history = p.get(f"bookings/{booking}/history", subject="pat").content.decode()
    assert "change_withdrawn" in history or "this appointment stays" in history


def test_nothing_to_take_back_is_refused(p):
    booking = confirmed(p)
    assert p.post(f"bookings/{booking}/keep", {}, subject="pat").status_code == 409


def test_the_clinic_answers_the_request_and_cannot_take_it_back_for_the_patient(p):
    booking = confirmed(p)
    p.post(f"bookings/{booking}/cancel", {}, subject="pat")
    assert p.post(f"bookings/{booking}/keep", {}, subject="ops").status_code == 403
    assert state(p, booking)["pending_change"] == "cancel"


def test_once_the_clinic_has_cancelled_it_cannot_be_kept(p):
    booking = confirmed(p)
    p.post(f"bookings/{booking}/cancel", {}, subject="pat")
    assert p.post(f"bookings/{booking}/cancel", {}, subject="ops").status_code == 200
    assert p.post(f"bookings/{booking}/keep", {}, subject="pat").status_code == 409


# ------------------------------------------------------------------ Zoiko staff two-step
def blockers(w, rel):
    r = call(w, "GET", f"platform/readiness/{rel['public_id']}", who="sam")
    return json.loads(r.content)["release"]["blockers"]


def test_a_release_waits_until_zoiko_staff_must_use_two_step(world):
    require_zoiko_two_step(world, required=False)
    rel = release(world)
    assert "g3.zoiko_two_step:missing" in blockers(world, rel)
    require_zoiko_two_step(world, required=True)
    assert "g3.zoiko_two_step:missing" not in blockers(world, rel)
