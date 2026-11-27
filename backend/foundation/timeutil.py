"""Time handling. Every module uses these helpers; nobody calls datetime.now() directly.

Rules (GHE-10 section 4, GHE-12 section 11):
1. We store instants in UTC.
2. For anything that came from, or is for, the real world (appointments, results, partner
   messages) we ALSO keep the original zone name, the offset in force, and the precision.
3. A naive datetime (no zone) is a bug. We reject it instead of guessing.
4. A date-only value ("2026-10-09") is NOT an instant. It stays a date.
5. A wall-clock time that does not exist (spring-forward) or exists twice (fall-back) is never
   silently fixed. The caller must say what to do.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = dt.timezone.utc  # noqa: UP017 - runtime target is 3.10-compatible


# --------------------------------------------------------------------------- errors


class TimeError(ValueError):
    """Base class for every time-handling problem."""


class NaiveDatetimeError(TimeError):
    """A datetime without a time zone was supplied."""


class UnknownTimeZoneError(TimeError):
    """The IANA zone name is not known."""


class AmbiguousLocalTimeError(TimeError):
    """The wall-clock time happens twice (clocks went back). Caller must choose."""


class NonexistentLocalTimeError(TimeError):
    """The wall-clock time never happens (clocks jumped forward)."""


class MissingOffsetError(TimeError):
    """A timestamp had a time but no offset, and no default zone was given."""


class InvalidTimestampError(TimeError):
    """The text is not a supported ISO 8601 value."""


# --------------------------------------------------------------------------- clock


class Clock(Protocol):
    def now(self) -> dt.datetime: ...


class SystemClock:
    """The real clock. Always returns an aware UTC time."""

    def now(self) -> dt.datetime:
        return dt.datetime.now(UTC)


class FixedClock:
    """A clock you control. Tests use it so 'now' never changes under their feet."""

    def __init__(self, instant: dt.datetime) -> None:
        self._instant = to_utc(instant)

    def now(self) -> dt.datetime:
        return self._instant

    def advance(self, delta: dt.timedelta) -> None:
        self._instant = self._instant + delta


_SYSTEM_CLOCK = SystemClock()


def utc_now(clock: Clock | None = None) -> dt.datetime:
    """Current time as an aware UTC datetime. Pass a clock in tests."""
    return (clock or _SYSTEM_CLOCK).now()


# --------------------------------------------------------------------------- basics


def require_aware(value: dt.datetime) -> dt.datetime:
    """Return value unchanged if it carries a real offset, otherwise raise."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDatetimeError("datetime has no time zone; refusing to guess")
    return value


def to_utc(value: dt.datetime) -> dt.datetime:
    return require_aware(value).astimezone(UTC)


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:  # ValueError: malformed keys like "../x"
        raise UnknownTimeZoneError(f"unknown time zone: {name!r}") from exc


def to_local(instant: dt.datetime, zone_name: str) -> dt.datetime:
    """Show an instant on the wall clock of a zone (for display only; never store this)."""
    return to_utc(instant).astimezone(get_zone(zone_name))


# --------------------------------------------------------------------------- precision


class Precision(str, Enum):
    """How exact a source's time is. A date-only value must stay date-only."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    MINUTE = "minute"
    SECOND = "second"


@dataclass(frozen=True)
class SourceDate:
    """A calendar date (maybe only a year or month). NOT an instant: no time, no zone."""

    value: dt.date
    precision: Precision

    def __post_init__(self) -> None:
        if self.precision not in (Precision.YEAR, Precision.MONTH, Precision.DAY):
            raise TimeError("SourceDate precision must be year, month or day")


@dataclass(frozen=True)
class SourceTime:
    """An instant plus what we know about where it came from.

    instant            always UTC. Use this to compare and sort.
    source_tz          IANA name if known (e.g. 'Europe/London'); None if the source only gave
                       a fixed offset like '+01:00' (a fixed offset does not tell us the zone).
    utc_offset_minutes the offset that was in force at the source at that moment.
    fold               1 means "the second occurrence" of a repeated wall-clock time.
    """

    instant: dt.datetime
    precision: Precision
    source_tz: str | None
    utc_offset_minutes: int
    fold: int = 0

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None or self.instant.utcoffset() != dt.timedelta(0):
            raise TimeError("SourceTime.instant must be an aware UTC datetime")
        if self.precision not in (Precision.MINUTE, Precision.SECOND):
            raise TimeError("SourceTime precision must be minute or second")
        if self.fold not in (0, 1):
            raise TimeError("fold must be 0 or 1")


# --------------------------------------------------------------------------- local times


class Ambiguity(str, Enum):
    REJECT = "reject"  # default: refuse and make the caller decide
    EARLIER = "earlier"  # first occurrence (still on summer time)
    LATER = "later"  # second occurrence (back on winter time)


def _offset_minutes(value: dt.datetime) -> int:
    offset = value.utcoffset()
    assert offset is not None  # noqa: S101 - callers pass aware values
    seconds = offset.total_seconds()
    if seconds % 60:
        raise TimeError("sub-minute UTC offsets are not supported")
    return int(seconds // 60)


def from_local(
    local: dt.datetime,
    zone_name: str,
    *,
    precision: Precision = Precision.SECOND,
    ambiguity: Ambiguity = Ambiguity.REJECT,
) -> SourceTime:
    """Turn a wall-clock time in a named zone into a SourceTime.

    `local` must be NAIVE (it is the number on the clock). The zone says which clock.
    Spring-forward gaps are always rejected. Fall-back repeats are rejected unless the caller
    chose EARLIER or LATER.
    """
    if local.tzinfo is not None:
        raise TimeError("from_local expects a naive wall-clock time; zone is a separate argument")
    zone = get_zone(zone_name)
    first = local.replace(tzinfo=zone, fold=0)
    second = local.replace(tzinfo=zone, fold=1)

    chosen_fold = 0
    if first.utcoffset() != second.utcoffset():
        # The offset changes around this wall time, so it is either a gap or a repeat.
        # Test: convert to UTC and back. If we land on the same wall time it exists (a repeat);
        # if it moved, it was in a gap.
        round_trip = first.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip != local:
            raise NonexistentLocalTimeError(f"{local.isoformat()} does not exist in {zone_name}")
        if ambiguity is Ambiguity.REJECT:
            raise AmbiguousLocalTimeError(
                f"{local.isoformat()} happens twice in {zone_name}; choose EARLIER or LATER"
            )
        chosen_fold = 0 if ambiguity is Ambiguity.EARLIER else 1

    resolved = local.replace(tzinfo=zone, fold=chosen_fold)
    return SourceTime(
        instant=resolved.astimezone(UTC),
        precision=precision,
        source_tz=zone_name,
        utc_offset_minutes=_offset_minutes(resolved),
        fold=chosen_fold,
    )


def from_offset_aware(value: dt.datetime, precision: Precision = Precision.SECOND) -> SourceTime:
    """For values that arrive with a fixed offset only (like '+01:00'). Zone name stays unknown."""
    require_aware(value)
    return SourceTime(
        instant=value.astimezone(UTC),
        precision=precision,
        source_tz=None,
        utc_offset_minutes=_offset_minutes(value),
    )


# --------------------------------------------------------------------------- parsing

_YEAR = re.compile(r"^\d{4}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MINUTE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?$")


def parse_source_value(text: str, *, default_zone: str | None = None) -> SourceTime | SourceDate:
    """Parse an ISO 8601 value from a partner without inventing information.

    - '2026' / '2026-10' / '2026-10-09'      -> SourceDate (stays a date)
    - '2026-10-09T10:30:00+01:00' or '...Z'  -> SourceTime with that offset (zone name unknown)
    - '2026-10-09T10:30:00' (no offset)      -> only if default_zone is given: then it is treated
                                                 as wall-clock time in that zone (strict gap and
                                                 repeat rules); otherwise MissingOffsetError
    """
    text = text.strip()
    try:
        if _YEAR.match(text):
            return SourceDate(dt.date(int(text), 1, 1), Precision.YEAR)
        if _MONTH.match(text):
            return SourceDate(dt.date(int(text[:4]), int(text[5:7]), 1), Precision.MONTH)
        if _DAY.match(text):
            return SourceDate(dt.date.fromisoformat(text), Precision.DAY)

        precision = Precision.MINUTE if _MINUTE_ONLY.match(text) else Precision.SECOND
        # Python 3.10's fromisoformat does not understand a trailing 'Z'.
        parsed = dt.datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        if isinstance(exc, TimeError):
            raise
        raise InvalidTimestampError("not a supported ISO 8601 value") from exc

    if parsed.tzinfo is not None:
        return from_offset_aware(parsed, precision)
    if default_zone is None:
        raise MissingOffsetError("timestamp has no offset and no default zone was provided")
    return from_local(parsed, default_zone, precision=precision)
