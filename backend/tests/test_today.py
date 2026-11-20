import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import UTC, FixedClock
from modules.scheduling import api as scheduling
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

MONDAY = "2027-09-06T09:00:00"                                     # 09:00 on the clinic's clock
# 08:00 on the clinic's clock that Monday (the clinics here are in Asia/Kolkata, UTC+5:30)
EARLY = FixedClock(dt.datetime(2027, 9, 6, 2, 30, tzinfo=UTC))
LATE = FixedClock(dt.datetime(2027, 9, 6, 10, 0, tzinfo=UTC))      # 15:30 that day


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def make(p, first=MONDAY, count=1, days=1):
    return p.post(f"services/{p.clinic.service}/slots",
                  {"location": p.clinic.location, "first_local": first, "count": count,
                   "days": days, "provider": p.doc}, subject="ops")


def counts(p, clock):
    with tenant_transaction(p.clinic.ctx):
        return scheduling.today_counts(p.clinic.org, clock)


def slots(p):
    return sorted((s for s in p.open_slots() if s["local_start"].startswith("2027-09-0")),
                  key=lambda s: s["local_start"])


def take(p, slot, confirm):
    booking = json.loads(book(p, slot["id"], subject="pat").content)["booking"]["id"]
    if confirm:
        p.post(f"bookings/{booking}/confirm", subject="ops")
    return booking


def test_a_quiet_day_is_all_zeros(p):
    assert counts(p, EARLY) == {"confirmed": 0, "waiting": 0, "still_to_come": 0}


def test_confirmed_and_waiting_appointments_of_today_are_counted_apart(p):
    make(p, count=2)
    first, second = slots(p)
    take(p, first, confirm=True)
    take(p, second, confirm=False)
    assert counts(p, EARLY) == {"confirmed": 1, "waiting": 1, "still_to_come": 2}


def test_an_appointment_that_has_started_is_not_still_to_come(p):
    make(p, count=1)
    take(p, slots(p)[0], confirm=True)
    assert counts(p, LATE) == {"confirmed": 1, "waiting": 0, "still_to_come": 0}


def test_a_cancelled_appointment_is_not_counted(p):
    make(p, count=1)
    booking = take(p, slots(p)[0], confirm=True)
    p.post(f"bookings/{booking}/cancel", subject="pat")
    p.post(f"bookings/{booking}/cancel", subject="ops")          # the clinic answers
    assert counts(p, EARLY) == {"confirmed": 0, "waiting": 0, "still_to_come": 0}


def test_other_days_are_not_counted(p):
    make(p, count=1, days=2)                                       # Monday and Tuesday
    tuesday = [s for s in slots(p) if s["local_start"].startswith("2027-09-07")][0]
    take(p, tuesday, confirm=True)
    assert counts(p, EARLY) == {"confirmed": 0, "waiting": 0, "still_to_come": 0}


def test_the_clinics_day_decides_not_utc(p):
    # 02:00 on the clinic's clock on Monday is still Sunday 20:30 in UTC: it is a Monday visit
    make(p, first="2027-09-06T02:00:00", count=1)
    take(p, slots(p)[0], confirm=True)
    assert counts(p, EARLY)["confirmed"] == 1


def test_another_clinics_appointments_are_not_counted(p):
    make(p, count=1)
    take(p, slots(p)[0], confirm=True)
    with tenant_transaction(p.clinic.ctx):
        assert scheduling.today_counts("not-this-clinic", EARLY) == {
            "confirmed": 0, "waiting": 0, "still_to_come": 0}
    assert counts(p, EARLY)["confirmed"] == 1


def test_the_staff_dashboard_carries_the_numbers(p):
    r = p.get("staff/dashboard", subject="ops")
    assert r.status_code == 200
    assert set(json.loads(r.content)["today"]) == {"confirmed", "waiting", "still_to_come"}
