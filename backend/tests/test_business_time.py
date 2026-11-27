"""Business hours: deadlines that count only the minutes a clinic is open (no database needed)."""
import datetime as dt

import pytest

from foundation.business_time import BusinessHours, BusinessHoursError, add_business_minutes

UTC = dt.timezone.utc
OFFICE = BusinessHours.from_spec({"days": [0, 1, 2, 3, 4], "opens": "09:00", "closes": "17:00"})


def at(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_minutes_inside_one_day_just_add_up():
    assert add_business_minutes(at("2026-11-23T10:00"), 60, OFFICE) == at("2026-11-23T11:00")


def test_a_deadline_that_runs_past_closing_continues_the_next_morning():
    assert add_business_minutes(at("2026-11-27T16:30"), 60, OFFICE) == at("2026-11-30T09:30")  # Fri


def test_time_outside_opening_hours_does_not_count():
    assert add_business_minutes(at("2026-11-23T07:00"), 30, OFFICE) == at("2026-11-23T09:30")
    assert add_business_minutes(at("2026-11-23T20:00"), 30, OFFICE) == at("2026-11-24T09:30")
    assert add_business_minutes(at("2026-11-21T12:00"), 30, OFFICE) == at("2026-11-23T09:30")  # Sat
    assert add_business_minutes(at("2026-11-22T12:00"), 30, OFFICE) == at("2026-11-23T09:30")  # Sun


def test_a_start_exactly_at_closing_counts_from_the_next_opening():
    assert add_business_minutes(at("2026-11-23T17:00"), 1, OFFICE) == at("2026-11-24T09:01")


def test_filling_the_day_exactly_ends_at_closing_time():
    assert add_business_minutes(at("2026-11-23T16:00"), 60, OFFICE) == at("2026-11-23T17:00")


def test_a_long_deadline_skips_every_weekend_in_between():
    # Three working days of eight hours from Friday 09:00 are Friday, Monday and Tuesday, so the
    # deadline is Tuesday at closing time.
    start = at("2026-11-27T09:00")
    assert add_business_minutes(start, 3 * 8 * 60, OFFICE) == at("2026-12-01T17:00")


def test_zero_minutes_returns_the_start_even_when_closed():
    assert add_business_minutes(at("2026-11-21T03:00"), 0, OFFICE) == at("2026-11-21T03:00")


def test_listed_holidays_are_skipped():
    hours = BusinessHours.from_spec({"holidays": ["2026-11-30"]})                              # Mon
    assert add_business_minutes(at("2026-11-27T16:00"), 120, hours) == at("2026-12-01T10:00")


def test_a_six_day_clinic_counts_saturdays():
    hours = BusinessHours.from_spec(
        {"days": [0, 1, 2, 3, 4, 5], "opens": "08:00", "closes": "12:00"})
    assert add_business_minutes(at("2026-11-28T11:00"), 120, hours) == at("2026-11-30T09:00")


def test_opening_hours_are_read_on_the_clinics_own_clock_across_a_clock_change():
    # London moves to summer time on Sunday 29 March 2026. Friday 16:00 is still winter time (UTC);
    # Monday 09:00 is summer time (08:00 UTC).
    got = add_business_minutes(at("2026-03-27T16:00"), 120, OFFICE, "Europe/London")
    assert got == at("2026-03-30T09:00")                       # 10:00 BST
    assert got.tzinfo == UTC


def test_another_zone_gives_another_instant():
    # 09:00 in New York in November is 14:00 UTC.
    got = add_business_minutes(at("2026-11-23T05:00"), 30, OFFICE, "America/New_York")
    assert got == at("2026-11-23T14:30")


@pytest.mark.parametrize("spec", [
    {"days": []}, {"days": [7]}, {"days": [-1]}, {"days": [True]}, {"days": "mon"},
    {"opens": "17:00", "closes": "09:00"}, {"opens": "09:00", "closes": "09:00"},
    {"opens": "nine"}, {"closes": 5}, {"holidays": ["not a date"]}, {"holidays": "2026-12-25"},
    {"holidays": ["2026-01-01"] * 400}, "everyday",
])
def test_an_unusable_calendar_is_refused(spec):
    with pytest.raises(BusinessHoursError):
        BusinessHours.from_spec(spec)


@pytest.mark.parametrize("minutes", [-1, True, 1.5, "30", 60 * 24 * 366])
def test_a_bad_number_of_minutes_is_refused(minutes):
    with pytest.raises(BusinessHoursError):
        add_business_minutes(at("2026-11-23T10:00"), minutes, OFFICE)


def test_a_start_without_a_time_zone_and_an_unknown_zone_are_refused():
    with pytest.raises(BusinessHoursError):
        add_business_minutes(dt.datetime(2026, 11, 23, 10, 0), 5, OFFICE)
    with pytest.raises(BusinessHoursError):
        add_business_minutes(at("2026-11-23T10:00"), 5, OFFICE, "Mars/Olympus")


def test_a_calendar_survives_being_stored_as_plain_data():
    hours = BusinessHours.from_spec(
        {"days": [4, 0, 0], "opens": "08:30", "closes": "16:45:30", "holidays": ["2026-12-25"]})
    assert hours.spec == {
        "days": [0, 4], "opens": "08:30", "closes": "16:45", "holidays": ["2026-12-25"]}
    assert BusinessHours.from_spec(hours.spec) == hours


def test_the_defaults_are_a_weekday_nine_to_five():
    assert BusinessHours.from_spec({}).spec == {
        "days": [0, 1, 2, 3, 4], "opens": "09:00", "closes": "17:00", "holidays": []}
