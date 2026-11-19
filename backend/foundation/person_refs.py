"""Moving a patient's records from one patient record to another, and back (merge and unmerge).

Many modules keep a `person_ref` (the patient's public id) on their rows. Trust cannot import those
modules, so each one registers the model it owns here at start-up, and trust drives the move.

Rules:
* Only rows of the bound customer are touched, and only rows that still point at the patient the
  move starts from.
* A row that cannot move because it would break a uniqueness rule (the other record already has the
  same booking-type row, say) is left where it is and reported, never forced.
* The ids of the rows that moved are returned so the caller can journal them: unmerge moves back
  exactly those rows, and nothing created later.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import F, Model
from django.utils import timezone

_holders: dict[str, Any] = {}      # label -> the model class (it has `objects` and `person_ref`)


def register(label: str, model: type[Model]) -> None:
    """Called once at start-up by a module whose model has a `person_ref` field."""
    _holders[label] = model


def holders() -> dict[str, Any]:
    return dict(_holders)


def _move_one(model: Any, pk: Any, scope: dict[str, Any], src: str, dst: str) -> bool:
    try:
        with transaction.atomic():
            changed = model.objects.filter(pk=pk, person_ref=src, **scope).update(
                person_ref=dst, entity_version=F("entity_version") + 1,
                updated_at=timezone.now())
    except IntegrityError:
        return False
    return bool(changed)


def repoint(*, src: str, dst: str, scope: dict[str, Any]) -> tuple[list[tuple[str, str]], int]:
    """Move every registered row from patient `src` to patient `dst`. Returns the (model label,
    row id) pairs that moved and how many were left behind."""
    moved: list[tuple[str, str]] = []
    left = 0
    for label, model in sorted(_holders.items()):
        for pk in list(model.objects.filter(person_ref=src, **scope).values_list("pk", flat=True)):
            if _move_one(model, pk, scope, src, dst):
                moved.append((label, str(pk)))
            else:
                left += 1
    return moved, left


def restore(
    *, items: Iterable[tuple[str, str]], src: str, dst: str, scope: dict[str, Any]
) -> tuple[int, int]:
    """Move exactly these rows back from `src` to `dst`. A row that has since gone elsewhere or
    cannot move is left. Returns (moved back, left)."""
    back = left = 0
    for label, pk in items:
        model = _holders.get(label)
        if model is None or not _move_one(model, pk, scope, src, dst):
            left += 1
        else:
            back += 1
    return back, left
