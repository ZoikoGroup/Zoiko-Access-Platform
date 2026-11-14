"""Reports count days on the clinic's own clock: the time zone its places share. A clinic whose
places use different zones, or that has none, falls back to UTC and the report says so."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import get_zone, utc_now
from modules.catalog import api as catalog
from modules.scheduling.models import Booking
from tests.test_clinician import Practice
from tests.test_reports import make_booking, report

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

KOLKATA = get_zone("Asia/Kolkata")


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.name_referral_owner()
    return practice


def add_place(p, name, zone):
    r = p.post("locations", {"organization": p.clinic.org, "name": name, "time_zone": zone},
               subject="ops")
    assert r.status_code == 201, r.content


def local_today(zone=KOLKATA):
    return utc_now().astimezone(zone).date()


def made_at(p, booking, instant):
    with tenant_transaction(p.clinic.ctx):
        Booking.objects.filter(public_id=booking).update(created_at=instant)


def local_midnight(day, zone=KOLKATA):
    return dt.datetime.combine(day, dt.time.min, tzinfo=zone).astimezone(dt.UTC)


# ------------------------------------------------------------------ the clinic's own clock
def test_the_report_says_which_clock_its_days_run_on(p):
    r = report(p)
    assert (r["zone"], r["zone_source"]) == ("Asia/Kolkata", "places")


def test_the_period_is_whole_days_ending_with_the_clinics_today(p):
    r = report(p)
    days = r["bookings"]["per_day"]
    today = local_today()
    assert len(days) == 30
    assert days[-1]["date"] == today.isoformat()
    assert days[0]["date"] == (today - dt.timedelta(days=29)).isoformat()
    assert (r["from"], r["to"]) == (days[0]["date"], days[-1]["date"])


def test_a_booking_made_just_after_the_clinics_midnight_counts_on_the_clinics_day(p):
    booking, _ = make_booking(p, confirm=False)
    yesterday = local_today() - dt.timedelta(days=1)
    instant = local_midnight(yesterday) + dt.timedelta(minutes=10)
    assert instant.date() != yesterday          # in UTC it falls on the day before: the old way
    made_at(p, booking, instant)
    per_day = {d["date"]: d["count"] for d in report(p, days=7)["bookings"]["per_day"]}
    assert per_day[yesterday.isoformat()] == 1
    assert per_day[(yesterday - dt.timedelta(days=1)).isoformat()] == 0


def test_a_booking_made_just_before_the_clinics_midnight_stays_on_that_day(p):
    booking, _ = make_booking(p, confirm=False)
    yesterday = local_today() - dt.timedelta(days=1)
    instant = local_midnight(yesterday + dt.timedelta(days=1)) - dt.timedelta(minutes=10)
    made_at(p, booking, instant)
    per_day = {d["date"]: d["count"] for d in report(p, days=7)["bookings"]["per_day"]}
    assert per_day[yesterday.isoformat()] == 1
    assert per_day[local_today().isoformat()] == 0


def test_every_booking_made_in_the_period_is_counted_exactly_once(p):
    for _ in range(3):
        make_booking(p, confirm=False)
    r = report(p)["bookings"]
    assert r["total"] == 3 and sum(d["count"] for d in r["per_day"]) == 3


def test_a_booking_older_than_the_period_is_not_counted(p):
    booking, _ = make_booking(p, confirm=False)
    made_at(p, booking, local_midnight(local_today() - dt.timedelta(days=40)))
    r = report(p, days=7)["bookings"]
    assert r["total"] == 0 and sum(d["count"] for d in r["per_day"]) == 0


# ------------------------------------------------------------------ when there is no single clock
def test_places_in_the_same_zone_are_still_one_clock(p):
    add_place(p, "Annex", "Asia/Kolkata")
    assert report(p)["zone"] == "Asia/Kolkata"


def test_places_in_different_zones_fall_back_to_utc_and_say_why(p):
    add_place(p, "Annex", "America/New_York")
    r = report(p)
    assert (r["zone"], r["zone_source"]) == ("UTC", "mixed")
    assert r["bookings"]["per_day"][-1]["date"] == local_today(dt.UTC).isoformat()


def test_a_clinic_with_no_place_falls_back_to_utc_and_says_why(p):
    with tenant_transaction(p.clinic.ctx):
        assert catalog.organization_zone("nothingnothingnothing0") == {
            "zone": "UTC", "source": "none"}


def test_a_utc_clinic_counts_days_as_before(p):
    add_place(p, "Annex", "America/New_York")        # now two zones: UTC
    booking, _ = make_booking(p, confirm=False)
    yesterday = local_today(dt.UTC) - dt.timedelta(days=1)
    made_at(p, booking, local_midnight(yesterday, dt.UTC) + dt.timedelta(hours=1))
    per_day = {d["date"]: d["count"] for d in report(p, days=7)["bookings"]["per_day"]}
    assert per_day[yesterday.isoformat()] == 1


# ------------------------------------------------------------------ what it does not change
def test_the_other_numbers_and_the_weekday_table_are_still_there(p):
    make_booking(p)
    r = report(p)
    assert len(r["bookings"]["by_weekday"]) == 7
    assert {"referrals", "waiting_list", "messages", "forecast"} <= set(r)


def test_only_staff_with_the_analytics_right_can_read_the_report(p):
    for who in ("pat", "doc"):
        assert p.get("staff/reports", subject=who).status_code == 403


def test_a_bad_number_of_days_is_still_refused(p):
    for days in ("3", "91", "x"):
        assert p.get(f"staff/reports?days={days}", subject="ops").status_code == 422
    assert json.loads(p.get("staff/reports?days=7", subject="ops").content)["days"] == 7
