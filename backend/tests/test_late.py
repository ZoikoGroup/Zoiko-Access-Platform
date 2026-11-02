import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.messaging.models import Thread
from modules.scheduling.models import Booking
from tests.test_attention import booked
from tests.test_clinician import Practice
from tests.test_messaging import opened, started

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def late(p):
    r = p.get("staff/dashboard", subject="ops")
    assert r.status_code == 200, r.content
    return json.loads(r.content)["late"]


def hours_ago(p, model, public_id, hours, field):
    with tenant_transaction(p.clinic.ctx):
        model.objects.filter(public_id=public_id).update(
            **{field: utc_now() - dt.timedelta(hours=hours)})


def test_nothing_is_late_when_nothing_waits(p):
    assert late(p) == {"hours": 24, "bookings": 0, "messages": 0}


def test_a_booking_is_late_only_after_the_target_has_passed(p):
    booking = booked(p, confirm=False)
    assert late(p)["bookings"] == 0                       # just made
    hours_ago(p, Booking, booking, 23, "created_at")
    assert late(p)["bookings"] == 0                       # still inside the target
    hours_ago(p, Booking, booking, 25, "created_at")
    assert late(p)["bookings"] == 1


def test_confirming_a_late_booking_clears_it(p):
    booking = booked(p, confirm=False)
    hours_ago(p, Booking, booking, 30, "created_at")
    assert late(p)["bookings"] == 1
    p.post(f"bookings/{booking}/confirm", subject="ops")
    assert late(p)["bookings"] == 0


def test_a_message_is_late_only_after_the_target_and_clears_when_opened(p):
    thread = started(p)
    assert late(p)["messages"] == 0
    hours_ago(p, Thread, thread, 30, "last_message_at")
    assert late(p)["messages"] == 1
    opened(p, thread, subject="ops")                      # the clinic reads it
    assert late(p)["messages"] == 0


def test_the_target_comes_from_the_setting(p, settings):
    settings.RESPONSE_TARGET_HOURS = 2
    booking = booked(p, confirm=False)
    hours_ago(p, Booking, booking, 3, "created_at")
    assert late(p) == {"hours": 2, "bookings": 1, "messages": 0}


def listed(p, booking):
    rows = json.loads(p.get("bookings", subject="ops").content)["bookings"]
    return next(r for r in rows if r["id"] == booking)


def test_the_booking_list_marks_only_waiting_bookings_past_the_target(p):
    booking = booked(p, confirm=False)
    assert listed(p, booking)["late"] is False
    hours_ago(p, Booking, booking, 25, "created_at")
    assert listed(p, booking)["late"] is True
    p.post(f"bookings/{booking}/confirm", subject="ops")
    assert listed(p, booking)["late"] is False            # confirmed is never "late"


def services_listed(p, subject="pat"):
    r = p.get("services", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["services"]


def confirmed_after(p, hours):
    """Book, confirm, then backdate so the wait was `hours` long."""
    if not [x for x in p.open_slots() if x["provider"]]:
        p.add_times(p.doc, count=8)
    booking = booked(p, confirm=True)
    with tenant_transaction(p.clinic.ctx):
        made = utc_now() - dt.timedelta(hours=hours)
        Booking.objects.filter(public_id=booking).update(created_at=made)
    return booking


def test_patients_see_a_usual_time_only_once_there_are_enough_confirmations(p):
    for _ in range(4):
        confirmed_after(p, 3)
    assert all(s["usual_confirmation_hours"] is None for s in services_listed(p))   # 4 is too few
    confirmed_after(p, 3)
    shown = {s["usual_confirmation_hours"] for s in services_listed(p)}
    assert shown == {3.0}


def test_the_usual_time_is_the_middle_value_not_the_average(p):
    for h in (1, 1, 1, 1, 100):
        confirmed_after(p, h)
    assert {s["usual_confirmation_hours"] for s in services_listed(p)} == {1.0}


def test_old_confirmations_do_not_count(p):
    for _ in range(5):
        confirmed_after(p, 24 * 100)
    assert all(s["usual_confirmation_hours"] is None for s in services_listed(p))
