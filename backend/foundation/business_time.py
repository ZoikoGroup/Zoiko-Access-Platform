"""Business hours: a clinic's working days and opening times, so a deadline counts only the minutes
the clinic is open.

A deadline of "24 hours" for a clinic that is shut at weekends should not run out while nobody is
there. `add_business_minutes` moves a start time forward by a number of OPEN minutes: it skips
nights, non-working days and listed holidays. Everything is read on the clinic's own clock
(an IANA zone), and the result is an exact instant in UTC.

A calendar is plain data (`BusinessHours.from_spec` / `.spec`) so it can be stored on a row and
copied onto a journey at the moment it starts: changing the clinic's hours later never moves the
deadline of a journey already running.

Pure code: no database and no clock of its own (the caller gives the start time).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from foundation.timeutil import UTC, TimeError, get_zone

DEFAULT_DAYS = (0, 1, 2, 3, 4)               # Monday to Friday (Monday is 0)
MAX_HOLIDAYS = 366
_SEARCH_DAYS = 3660                          # ten years: more than any real deadline needs
MAX_MINUTES = 60 * 24 * 365


class BusinessHoursError(ValueError):
    """The calendar is not usable (no working day, opening not before closing, a bad date)."""


def _time(value: Any, label: str) -> dt.time:
    if isinstance(value, dt.time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    if isinstance(value, str):
        try:
            parsed = dt.time.fromisoformat(value)
        except ValueError:
            raise BusinessHoursError(label) from None
        return parsed.replace(second=0, microsecond=0, tzinfo=None)
    raise BusinessHoursError(label)


@dataclass(frozen=True)
class BusinessHours:
    days: tuple[int, ...]                    # working weekdays, Monday is 0
    opens: dt.time
    closes: dt.time
    holidays: frozenset[dt.date]

    def __post_init__(self) -> None:
        if not self.days or any(d not in range(7) for d in self.days):
            raise BusinessHoursError("days")
        if not self.opens < self.closes:
            raise BusinessHoursError("hours")

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> BusinessHours:
        """Build from plain data: {"days": [0..6], "opens": "09:00", "closes": "17:00",
        "holidays": ["2026-12-25", ...]}. Anything else is refused."""
        if not isinstance(spec, dict):
            raise BusinessHoursError("spec")
        days_raw = spec.get("days", DEFAULT_DAYS)
        if not isinstance(days_raw, (list, tuple)) or any(
                isinstance(d, bool) or not isinstance(d, int) for d in days_raw):
            raise BusinessHoursError("days")
        holidays_raw = spec.get("holidays", [])
        if not isinstance(holidays_raw, (list, tuple)) or len(holidays_raw) > MAX_HOLIDAYS:
            raise BusinessHoursError("holidays")
        try:
            holidays = frozenset(
                h if isinstance(h, dt.date) else dt.date.fromisoformat(str(h))
                for h in holidays_raw)
        except ValueError:
            raise BusinessHoursError("holidays") from None
        return cls(
            days=tuple(sorted(set(days_raw))), opens=_time(spec.get("opens", "09:00"), "opens"),
            closes=_time(spec.get("closes", "17:00"), "closes"), holidays=holidays)

    @property
    def spec(self) -> dict[str, Any]:
        return {
            "days": list(self.days), "opens": self.opens.strftime("%H:%M"),
            "closes": self.closes.strftime("%H:%M"),
            "holidays": sorted(h.isoformat() for h in self.holidays),
        }

    def is_working_day(self, day: dt.date) -> bool:
        return day.weekday() in self.days and day not in self.holidays


def add_business_minutes(
    start: dt.datetime, minutes: int, hours: BusinessHours, zone: str = "UTC"
) -> dt.datetime:
    """The instant `minutes` OPEN minutes after `start`, counting only time inside the clinic's
    opening hours on its working days (read on the clock of `zone`). Zero minutes returns `start`
    itself, even outside opening hours. The result is in UTC."""
    if start.tzinfo is None:
        raise BusinessHoursError("start must carry a time zone")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= MAX_MINUTES:
        raise BusinessHoursError("minutes")
    try:
        local_zone = get_zone(zone)
    except TimeError:
        raise BusinessHoursError("zone") from None
    if minutes == 0:
        return start.astimezone(UTC)
    remaining = dt.timedelta(minutes=minutes)
    cursor = start.astimezone(UTC)
    day = cursor.astimezone(local_zone).date()
    for _ in range(_SEARCH_DAYS):
        if hours.is_working_day(day):
            opens = dt.datetime.combine(day, hours.opens, tzinfo=local_zone).astimezone(UTC)
            closes = dt.datetime.combine(day, hours.closes, tzinfo=local_zone).astimezone(UTC)
            begin = max(cursor, opens)
            if begin < closes:
                available = closes - begin
                if remaining <= available:
                    return begin + remaining
                remaining -= available
        day += dt.timedelta(days=1)
    raise BusinessHoursError("no open time found")
