"""What kinds of data the product keeps, and who is under a lawful hold (DEV-213).

Retention periods are a legal decision (DEV-013, decision 5 in docs/LEAD-NOTE.md) that each
clinic records for itself. Nothing is erased unless the deployment switches erasing on and a person
asks for it (DEV-351). Each module that keeps a kind
of data registers a COUNTER: given a cut-off instant and the patients under a lawful hold, how many
of its records for the bound customer are older than the cut-off. The rights module registers who is
under a hold. `modules.retention` reads both to report what WOULD be due under a period, once Legal
sets one. Registries, like `foundation.rotation`, so foundation never imports a domain module.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

# (cut-off, person refs on hold) -> number of this customer's records older than the cut-off,
# leaving out every record of a person on hold. Counts only: never the records themselves.
Counter = Callable[[dt.datetime, frozenset[str]], int]


@dataclass(frozen=True)
class Eraser:
    """How a kind of data is erased, for ONE clinic: `due` counts what would go, `erase` removes it
    and returns how many. Both take the cut-off, the patients on hold and the clinic."""

    due: Callable[[dt.datetime, frozenset[str], str], int]
    erase: Callable[[dt.datetime, frozenset[str], str], int]


@dataclass(frozen=True)
class DataClass:
    key: str            # e.g. "bookings"
    label: str          # plain words for the screen
    age_from: str       # what the age is measured from, in plain words
    count: Counter
    eraser: Eraser | None = None      # None: this kind is never erased by the product


_classes: dict[str, DataClass] = {}
_holds: list[Callable[[], frozenset[str]]] = []


def register(
    key: str, label: str, age_from: str, count: Counter, eraser: Eraser | None = None
) -> None:
    if key in _classes and _classes[key].count is not count:
        raise ValueError(f"data class {key!r} is registered twice")
    _classes[key] = DataClass(key, label, age_from, count, eraser)


def register_holds(source: Callable[[], frozenset[str]]) -> None:
    if source not in _holds:
        _holds.append(source)


def data_classes() -> list[DataClass]:
    return [_classes[k] for k in sorted(_classes)]


def persons_on_hold() -> frozenset[str]:
    """Every patient a registered source says is under a lawful hold, for the bound customer."""
    held: set[str] = set()
    for source in _holds:
        held |= source()
    return frozenset(held)
