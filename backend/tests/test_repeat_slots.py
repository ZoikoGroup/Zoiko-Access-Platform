import json

import pytest

from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

MONDAY = "2027-09-06T09:00:00"
FRIDAY = "2027-09-10T09:00:00"
SATURDAY = "2027-09-11T09:00:00"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    # The test clinic already has some free times: remember them so the tests count only their own.
    practice.before = {s["id"] for s in practice.open_slots()}
    return practice


def mine(p):
    return [s for s in p.open_slots() if s["id"] not in p.before]


def make(p, first=MONDAY, count=2, subject="ops", **extra):
    body = {"location": p.clinic.location, "first_local": first, "count": count,
            "provider": p.doc, **extra}
    return p.post(f"services/{p.clinic.service}/slots", body, subject=subject)


def days_open(p):
    """The calendar days (clinic time) that have free times, in order, with how many each."""
    out: dict[str, int] = {}
    for s in sorted(mine(p), key=lambda s: s["local_start"]):
        out[s["local_start"][:10]] = out.get(s["local_start"][:10], 0) + 1
    return out


def test_one_day_is_the_old_behaviour(p):
    r = make(p, count=3)
    assert r.status_code == 201 and json.loads(r.content) == {"created": 3}
    assert days_open(p) == {"2027-09-06": 3}


def test_repeating_for_several_days_makes_the_same_times_each_day(p):
    r = make(p, count=2, days=3)
    assert r.status_code == 201 and json.loads(r.content) == {"created": 6}
    assert days_open(p) == {"2027-09-06": 2, "2027-09-07": 2, "2027-09-08": 2}
    starts = {s["local_start"][11:16] for s in mine(p)}
    assert starts == {"09:00", "09:30"}                      # the same two times every day


def test_weekdays_only_skips_the_weekend_and_does_not_use_up_a_day(p):
    make(p, first=FRIDAY, count=1, days=3, weekdays_only=True)
    assert list(days_open(p)) == ["2027-09-10", "2027-09-13", "2027-09-14"]   # Fri, Mon, Tue


def test_starting_on_a_weekend_with_weekdays_only_starts_on_monday(p):
    make(p, first=SATURDAY, count=1, days=2, weekdays_only=True)
    assert list(days_open(p)) == ["2027-09-13", "2027-09-14"]


def test_without_weekdays_only_weekends_are_included(p):
    make(p, first=FRIDAY, count=1, days=3)
    assert list(days_open(p)) == ["2027-09-10", "2027-09-11", "2027-09-12"]


@pytest.mark.parametrize("days", [0, -1, 32, 1.5, "3", None, True])
def test_a_bad_number_of_days_is_refused_and_nothing_is_made(p, days):
    assert make(p, days=days).status_code == 422
    assert days_open(p) == {}


def test_one_request_cannot_make_more_than_four_hundred_times(p):
    assert make(p, count=14, days=31).status_code == 422          # 434 times
    assert days_open(p) == {}
    assert make(p, count=12, days=31).status_code == 201          # 372 times is fine


@pytest.mark.parametrize("weekdays_only", ["yes", 1, None, "true"])
def test_weekdays_only_must_be_a_real_true_or_false(p, weekdays_only):
    assert make(p, days=2, weekdays_only=weekdays_only).status_code == 422
    assert days_open(p) == {}


def test_it_is_all_or_nothing_if_one_day_clashes(p):
    # the 8th already has a 09:00 time, so a three-day run from the 6th clashes on its last day
    assert make(p, first="2027-09-08T09:00:00", count=1).status_code == 201
    before = days_open(p)
    r = make(p, count=1, days=3)                                  # 6th, 7th, then 8th clashes
    assert r.status_code == 409
    assert days_open(p) == before                                 # the 6th and 7th were not kept


def test_a_start_in_the_past_is_refused_and_nothing_is_made(p):
    assert make(p, first="2020-01-06T09:00:00", days=3).status_code == 422
    assert days_open(p) == {}


def test_only_clinic_staff_may_do_it(p):
    for who in ("pat", "doc"):
        assert make(p, days=2, subject=who).status_code == 403
    assert days_open(p) == {}


def test_every_day_uses_the_clinics_own_wall_clock_time(p):
    # The same wall-clock time on each day, with no drift (the zone's clock-change refusal is
    # covered by the existing single-day tests).
    make(p, count=1, days=5)
    assert {s["local_start"][11:16] for s in mine(p)} == {"09:00"}
