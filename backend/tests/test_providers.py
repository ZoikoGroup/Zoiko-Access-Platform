import datetime as dt
import json

import pytest

from foundation.timeutil import utc_now
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def booked(p, confirm):
    """Three times for "doc" two days from now (inside the 14-day window); book the first."""
    soon = (utc_now() + dt.timedelta(days=2)).strftime("%Y-%m-%dT09:00:00")
    p.add_times(p.doc, first=soon, count=3)
    slot = p.slot_of("doc")
    booking = json.loads(book(p, slot, subject="pat").content)["booking"]["id"]
    if confirm:
        p.post(f"bookings/{booking}/confirm", subject="ops")
    return booking


def providers(p, subject="ops"):
    r = p.get("staff/providers", subject=subject)
    assert r.status_code == 200, r.content
    return {x["name"]: x for x in json.loads(r.content)["providers"]}


def test_every_doctor_of_the_clinic_is_listed_even_with_no_work(p):
    rows = providers(p)
    assert set(rows) == {"doc", "doc2"}
    assert rows["doc"] == {"id": p.doc, "name": "doc", "name_check": {"state": "none"},
                           "upcoming": 0, "open_times": 0, "next_local": None, "next_zone": None}


def test_free_times_and_confirmed_appointments_are_counted_per_doctor(p):
    booked(p, confirm=True)                                    # adds 3 times for "doc", books one
    rows = providers(p)
    assert rows["doc"]["upcoming"] == 1
    assert rows["doc"]["open_times"] == 2                      # 3 added, 1 taken
    assert rows["doc"]["next_local"] and rows["doc"]["next_zone"]
    assert rows["doc2"]["upcoming"] == 0 and rows["doc2"]["open_times"] == 0


def test_a_waiting_booking_is_not_counted_as_an_appointment_but_takes_the_time(p):
    booked(p, confirm=False)
    row = providers(p)["doc"]
    assert row["upcoming"] == 0 and row["open_times"] == 2


def test_a_cancelled_booking_gives_the_time_back(p):
    booking = booked(p, confirm=True)
    p.post(f"bookings/{booking}/cancel", subject="pat")
    p.post(f"bookings/{booking}/cancel", subject="ops")          # the clinic answers
    row = providers(p)["doc"]
    assert row["upcoming"] == 0 and row["open_times"] == 3


def test_the_list_holds_no_patient_data(p):
    booked(p, confirm=True)
    body = p.get("staff/providers", subject="ops").content.decode()
    assert p.person not in body and p.patient_actor not in body


def test_times_beyond_the_window_are_not_counted(p):
    p.add_times(p.doc, first="2027-09-01T09:00:00", count=3)      # far in the future
    assert providers(p)["doc"]["open_times"] == 0


def test_another_customers_doctors_are_not_listed(p):
    other = Practice()
    booked(other, confirm=True)
    assert providers(p)["doc"]["upcoming"] == 0                  # not the other clinic's booking
    assert providers(other)["doc"]["upcoming"] == 1 and other.doc != p.doc


def test_only_staff_may_see_it(p):
    for who in ("pat", "doc"):
        assert p.get("staff/providers", subject=who).status_code == 403
