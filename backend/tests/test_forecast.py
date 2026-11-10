import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import UTC, FixedClock
from modules.scheduling import api as scheduling
from modules.scheduling.services import enough_history, usual_by_weekday
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

TUE = dt.date(2027, 3, 2)           # a Tuesday


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


# ------------------------------------------------------------------ the average itself (pure)
def test_a_usual_weekday_is_the_average_of_that_weekday_with_empty_days_as_zero():
    past = {TUE - dt.timedelta(days=7): 4, TUE - dt.timedelta(days=14): 2}   # two earlier Tuesdays
    usual = usual_by_weekday(past, TUE, history_days=21)
    assert usual[1] == 2.0            # Tuesdays 21, 14 and 7 days back: (0 + 2 + 4) / 3
    assert usual[0] == 0.0 and usual[6] == 0.0
    assert set(usual) == set(range(7))


def test_only_days_before_today_count_and_history_is_bounded():
    past = {TUE: 50, TUE - dt.timedelta(days=7): 3, TUE - dt.timedelta(days=70): 90}
    assert usual_by_weekday(past, TUE, 14)[1] == 1.5       # today and 70 days back are ignored
    assert usual_by_weekday(past, TUE, 0) == {w: 0.0 for w in range(7)}


# ------------------------------------------------------------------ the report
def test_a_new_clinic_gets_no_forecast_only_what_is_booked(monkeypatch):
    p = Practice()
    with tenant_transaction(p.clinic.ctx):
        week = scheduling.forecast_week(p.clinic.org)
    assert week["enough_history"] is False and week["history_days"] == 0
    assert len(week["days"]) == 7 and all(d["usual"] is None for d in week["days"])
    assert sum(d["booked"] for d in week["days"]) == 0


def test_the_forecast_appears_in_the_staff_report_with_counts_only():
    p = Practice()
    data = json.loads(p.get("staff/reports?days=30", subject="ops").content)
    assert set(data["forecast"]) == {"enough_history", "history_days", "days"}
    assert set(data["forecast"]["days"][0]) == {"date", "weekday", "booked", "open", "usual"}
    assert "patient" not in json.dumps(data["forecast"]).lower()


def test_a_week_with_history_shows_booked_and_usual_per_day():
    p = Practice()
    p.add_times(p.doc, first="2027-03-02T09:00:00", count=2)       # a Tuesday: two free times
    now = dt.datetime(2027, 3, 1, 8, 0, tzinfo=UTC)
    with tenant_transaction(p.clinic.ctx):
        week = scheduling.forecast_week(p.clinic.org, FixedClock(now))
    assert [d["date"] for d in week["days"]][0] == "2027-03-01" and len(week["days"]) == 7
    tuesday = week["days"][1]
    assert tuesday["weekday"] == 1 and tuesday["booked"] == 0     # free times are not bookings
    assert tuesday["open"] == 2                       # both free times fall on the Tuesday
    assert week["days"][2]["open"] == 0


def test_a_usual_day_needs_both_enough_days_and_enough_bookings():
    assert enough_history(14, 10) is True
    assert enough_history(13, 500) is False         # too short a history, however busy
    assert enough_history(400, 9) is False          # long, but a handful of bookings is not "usual"
    assert enough_history(0, 0) is False
