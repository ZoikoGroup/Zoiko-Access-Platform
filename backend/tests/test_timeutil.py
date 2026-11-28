import datetime as dt

import pytest

from foundation.timeutil import (
    UTC,
    Ambiguity,
    AmbiguousLocalTimeError,
    FixedClock,
    InvalidTimestampError,
    MissingOffsetError,
    NaiveDatetimeError,
    NonexistentLocalTimeError,
    Precision,
    SourceDate,
    SourceTime,
    TimeError,
    UnknownTimeZoneError,
    from_local,
    from_offset_aware,
    parse_source_value,
    require_aware,
    to_local,
    to_utc,
    utc_now,
)


# ------------------------------------------------------------------ clock and basics
def test_utc_now_is_aware_and_utc():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == dt.timedelta(0)


def test_fixed_clock_does_not_move_until_told():
    clock = FixedClock(dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    assert utc_now(clock) == dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    clock.advance(dt.timedelta(minutes=5))
    assert utc_now(clock) == dt.datetime(2026, 10, 9, 12, 5, tzinfo=UTC)


def test_naive_datetime_is_rejected_not_guessed():
    with pytest.raises(NaiveDatetimeError):
        require_aware(dt.datetime(2026, 10, 9, 12, 0))
    with pytest.raises(NaiveDatetimeError):
        to_utc(dt.datetime(2026, 10, 9, 12, 0))
    with pytest.raises(NaiveDatetimeError):
        FixedClock(dt.datetime(2026, 10, 9, 12, 0))


def test_to_utc_converts_any_offset():
    plus_five_thirty = dt.timezone(dt.timedelta(hours=5, minutes=30))
    value = dt.datetime(2026, 10, 9, 17, 30, tzinfo=plus_five_thirty)
    assert to_utc(value) == dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ zones
def test_unknown_and_malformed_zone_names_are_rejected():
    with pytest.raises(UnknownTimeZoneError):
        from_local(dt.datetime(2026, 7, 1, 12, 0), "Mars/Olympus_Mons")
    with pytest.raises(UnknownTimeZoneError):
        from_local(dt.datetime(2026, 7, 1, 12, 0), "../etc/passwd")


def test_summer_time_london_converts_to_utc_and_keeps_zone_and_offset():
    result = from_local(dt.datetime(2026, 7, 1, 12, 0), "Europe/London")
    assert result.instant == dt.datetime(2026, 7, 1, 11, 0, tzinfo=UTC)
    assert result.source_tz == "Europe/London"
    assert result.utc_offset_minutes == 60
    assert result.fold == 0


def test_zone_without_daylight_saving():
    result = from_local(dt.datetime(2026, 7, 1, 12, 0), "Asia/Kolkata")
    assert result.instant == dt.datetime(2026, 7, 1, 6, 30, tzinfo=UTC)
    assert result.utc_offset_minutes == 330


def test_from_local_refuses_an_already_aware_value():
    with pytest.raises(TimeError):
        from_local(dt.datetime(2026, 7, 1, 12, 0, tzinfo=UTC), "Europe/London")


def test_round_trip_back_to_the_same_wall_clock():
    wall = dt.datetime(2026, 7, 1, 12, 0)
    stored = from_local(wall, "Europe/London")
    assert to_local(stored.instant, "Europe/London").replace(tzinfo=None) == wall


# ------------------------------------------------------------------ daylight-saving edges
@pytest.mark.parametrize(
    ("wall", "zone"),
    [
        (dt.datetime(2026, 3, 29, 1, 30), "Europe/London"),  # clocks jump 01:00 -> 02:00
        (dt.datetime(2026, 3, 8, 2, 30), "America/New_York"),  # clocks jump 02:00 -> 03:00
    ],
)
def test_spring_forward_gap_never_exists_and_is_rejected(wall, zone):
    with pytest.raises(NonexistentLocalTimeError):
        from_local(wall, zone)
    with pytest.raises(NonexistentLocalTimeError):  # even if the caller picks an ambiguity rule
        from_local(wall, zone, ambiguity=Ambiguity.EARLIER)


def test_fall_back_repeat_is_rejected_by_default():
    with pytest.raises(AmbiguousLocalTimeError):
        from_local(dt.datetime(2026, 10, 25, 1, 30), "Europe/London")
    with pytest.raises(AmbiguousLocalTimeError):
        from_local(dt.datetime(2026, 11, 1, 1, 30), "America/New_York")


def test_fall_back_repeat_can_be_resolved_explicitly():
    wall = dt.datetime(2026, 10, 25, 1, 30)  # 01:30 happens at 00:30Z (BST) and 01:30Z (GMT)
    earlier = from_local(wall, "Europe/London", ambiguity=Ambiguity.EARLIER)
    later = from_local(wall, "Europe/London", ambiguity=Ambiguity.LATER)
    assert earlier.instant == dt.datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert earlier.utc_offset_minutes == 60
    assert earlier.fold == 0
    assert later.instant == dt.datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert later.utc_offset_minutes == 0
    assert later.fold == 1
    assert earlier.instant < later.instant


def test_times_just_outside_the_edges_are_normal():
    before_gap = from_local(dt.datetime(2026, 3, 29, 0, 59), "Europe/London")
    after_gap = from_local(dt.datetime(2026, 3, 29, 2, 0), "Europe/London")
    assert before_gap.utc_offset_minutes == 0
    assert after_gap.utc_offset_minutes == 60


# ------------------------------------------------------------------ parsing partner values
def test_date_only_values_stay_dates_and_never_become_instants():
    day = parse_source_value("2026-10-09")
    month = parse_source_value("2026-10")
    year = parse_source_value("2026")
    assert isinstance(day, SourceDate) and day.precision is Precision.DAY
    assert isinstance(month, SourceDate) and month.precision is Precision.MONTH
    assert isinstance(year, SourceDate) and year.precision is Precision.YEAR
    assert day.value == dt.date(2026, 10, 9)


def test_timestamp_with_z_and_with_offset():
    z = parse_source_value("2026-10-09T10:30:00Z")
    offset = parse_source_value("2026-10-09T11:30:00+01:00")
    assert isinstance(z, SourceTime) and isinstance(offset, SourceTime)
    assert z.instant == offset.instant == dt.datetime(2026, 10, 9, 10, 30, tzinfo=UTC)
    assert z.source_tz is None  # a fixed offset does NOT tell us the zone
    assert offset.utc_offset_minutes == 60


def test_minute_precision_is_remembered():
    value = parse_source_value("2026-10-09T10:30Z")
    assert isinstance(value, SourceTime)
    assert value.precision is Precision.MINUTE


def test_fractional_seconds_are_accepted():
    value = parse_source_value("2026-10-09T10:30:00.123+00:00")
    assert isinstance(value, SourceTime)
    assert value.instant.microsecond == 123000


def test_timestamp_without_offset_is_refused_unless_a_zone_is_given():
    with pytest.raises(MissingOffsetError):
        parse_source_value("2026-10-09T10:30:00")
    resolved = parse_source_value("2026-10-09T10:30:00", default_zone="Europe/London")
    assert isinstance(resolved, SourceTime)
    assert resolved.instant == dt.datetime(2026, 10, 9, 9, 30, tzinfo=UTC)
    assert resolved.source_tz == "Europe/London"


def test_default_zone_still_obeys_the_gap_and_repeat_rules():
    with pytest.raises(NonexistentLocalTimeError):
        parse_source_value("2026-03-29T01:30:00", default_zone="Europe/London")
    with pytest.raises(AmbiguousLocalTimeError):
        parse_source_value("2026-10-25T01:30:00", default_zone="Europe/London")


@pytest.mark.parametrize(
    "bad",
    ["", "yesterday", "2026-13-01", "2026-02-30", "10/09/2026", "2026-10-09T25:00:00Z"],
)
def test_garbage_is_rejected_with_a_clear_error(bad):
    with pytest.raises(InvalidTimestampError):
        parse_source_value(bad)


# ------------------------------------------------------------------ value objects guard themselves
def test_source_time_requires_a_utc_instant():
    plus_one = dt.timezone(dt.timedelta(hours=1))
    with pytest.raises(TimeError):
        SourceTime(dt.datetime(2026, 1, 1, 12, 0, tzinfo=plus_one), Precision.SECOND, None, 60)
    with pytest.raises(TimeError):
        SourceTime(dt.datetime(2026, 1, 1, 12, 0), Precision.SECOND, None, 0)


def test_source_time_and_source_date_reject_wrong_precision():
    with pytest.raises(TimeError):
        SourceTime(dt.datetime(2026, 1, 1, tzinfo=UTC), Precision.DAY, None, 0)
    with pytest.raises(TimeError):
        SourceDate(dt.date(2026, 1, 1), Precision.SECOND)


def test_from_offset_aware_rejects_naive():
    with pytest.raises(NaiveDatetimeError):
        from_offset_aware(dt.datetime(2026, 1, 1, 12, 0))
