import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.scheduling.models import Slot
from tests.test_api import book
from tests.test_clinician import Practice
from tests.test_reports import in_the_past, make_booking, report

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def visit(p, outcome=None):
    """A confirmed appointment that already happened, with the outcome recorded if given."""
    booking, slot = make_booking(p)
    in_the_past(p, slot, days_ago=2)
    if outcome:
        p.post(f"bookings/{booking}/attendance", {"outcome": outcome}, subject="ops")
    return booking


def doctors(p):
    return report(p)["bookings"]["by_doctor"]


# ------------------------------------------------------------------ missed visits by doctor
def test_a_doctor_shows_attended_missed_not_recorded_and_the_missed_share(p):
    visit(p, "attended")
    visit(p, "attended")
    visit(p, "no_show")
    visit(p)                                                       # not recorded yet
    [row] = doctors(p)
    assert (row["attended"], row["no_show"], row["not_recorded"]) == (2, 1, 1)
    assert row["no_show_rate"] == pytest.approx(1 / 3, abs=0.001)  # only recorded visits count


def test_the_share_is_empty_when_nothing_was_recorded(p):
    visit(p)
    assert doctors(p)[0]["no_show_rate"] is None


def test_a_doctor_who_missed_every_recorded_visit_is_at_100_percent(p):
    visit(p, "no_show")
    assert doctors(p)[0]["no_show_rate"] == 1.0


def test_waiting_cancelled_and_future_appointments_are_not_visits(p):
    make_booking(p)                                                # confirmed but still to come
    waiting, w_slot = make_booking(p, confirm=False)
    gone, g_slot = make_booking(p, confirm=False)
    p.post(f"bookings/{gone}/cancel", subject="pat")
    for days_ago, slot in enumerate((w_slot, g_slot), start=2):
        in_the_past(p, slot, days_ago=days_ago)
    assert waiting
    assert doctors(p) == []


def visits_of(p, who, how_many):
    """`how_many` confirmed appointments that already happened, all with this doctor."""
    p.add_times(getattr(p, who), count=how_many)
    for n in range(how_many):
        slot = p.slot_of(who)
        booking = json.loads(book(p, slot, subject="pat").content)["booking"]["id"]
        p.post(f"bookings/{booking}/confirm", subject="ops")
        in_the_past(p, slot, days_ago=2 + n)


@pytest.mark.parametrize("busy,quiet", [("doc", "doc2"), ("doc2", "doc")])
def test_the_doctor_has_a_name_and_the_busiest_doctor_comes_first(p, busy, quiet):
    # either doctor is the busy one in turn, so the order cannot be explained by their ids
    visits_of(p, busy, 3)
    visits_of(p, quiet, 1)
    rows = doctors(p)
    assert [r["provider_ref"] for r in rows] == [getattr(p, busy), getattr(p, quiet)]
    assert [r["not_recorded"] for r in rows] == [3, 1]
    assert rows[0]["name"] and rows[0]["name"] != rows[1]["name"]


def test_another_clinics_doctors_are_not_listed(p):
    visit(p, "attended")
    other = Practice()
    other_booking, other_slot = make_booking(other)
    in_the_past(other, other_slot, days_ago=2)
    assert other_booking
    assert [r["provider_ref"] for r in doctors(p)] == [p.doc]


# ------------------------------------------------------------------ busiest days of the week
def put(p, slot, starts):
    with tenant_transaction(p.clinic.ctx):
        Slot.objects.filter(public_id=slot).update(
            starts_at=starts, ends_at=starts + dt.timedelta(minutes=30))


def week(p, days=30):
    return report(p, days=days)["bookings"]["by_weekday"]


def test_a_quiet_clinic_has_seven_zeros(p):
    assert week(p) == [0] * 7


def test_appointments_are_counted_on_the_clinics_own_weekday_not_utc(p):
    _, slot = make_booking(p)
    base = (utc_now() - dt.timedelta(days=3)).replace(hour=22, minute=0, second=0, microsecond=0)
    put(p, slot, base)                                             # 22:00 UTC = 03:30 next day IST
    expected = base.astimezone(IST).weekday()
    assert expected != base.weekday()                  # the guard: UTC would say otherwise
    counts = week(p)
    assert counts[expected] == 1 and sum(counts) == 1


def test_waiting_and_confirmed_count_but_cancelled_does_not(p):
    _, a = make_booking(p)
    _, b = make_booking(p, confirm=False)
    gone, c = make_booking(p, confirm=False)
    p.post(f"bookings/{gone}/cancel", subject="pat")
    noon = (utc_now() - dt.timedelta(days=2)).replace(hour=6, minute=0, second=0, microsecond=0)
    for hour, slot in enumerate((a, b, c)):
        put(p, slot, noon + dt.timedelta(hours=hour))
    assert sum(week(p)) == 2


def test_appointments_outside_the_period_are_left_out(p):
    _, slot = make_booking(p)
    put(p, slot, utc_now() - dt.timedelta(days=20))
    assert sum(week(p, days=30)) == 1
    assert sum(week(p, days=7)) == 0


def test_the_week_counts_appointments_in_the_period_even_if_booked_earlier(p):
    booking, slot = make_booking(p)
    from modules.scheduling.models import Booking
    with tenant_transaction(p.clinic.ctx):
        Booking.objects.filter(public_id=booking).update(
            created_at=utc_now() - dt.timedelta(days=60))
    put(p, slot, utc_now() - dt.timedelta(days=2))
    assert sum(week(p, days=30)) == 1
    assert report(p, days=30)["bookings"]["total"] == 0           # "made" is a different question


def test_the_new_numbers_identify_no_patient(p):
    p.post(f"people/{p.person}/profile", {"name": "Asha Rao"}, subject="pat")
    visit(p, "no_show")
    text = json.dumps(report(p))
    assert p.person not in text and "Asha" not in text
