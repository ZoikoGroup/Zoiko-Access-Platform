"""Moving every encrypted value onto the current key (DEV-122).

A key is rotated by adding a new one to ZOIKO_FIELD_KEYS and making it the current one
(ZOIKO_FIELD_KEY_ID). Old values keep working, because every listed key can still decrypt, but they
stay on the old key until this runs; only then can the old key be retired. Run it by hand:

    python manage.py rotate_field_keys --dry-run     # count what would change
    python manage.py rotate_field_keys               # do it

Each module that stores encrypted values registers a function here that does its own tables. It is
safe to run twice and to stop part-way: a row already on the current key is skipped, and each
tenant's tables are changed in one transaction (if it fails, nothing of that tenant changed: run it
again). A value that cannot be read is counted and left alone; nothing is deleted or guessed.
The plain text is never logged or returned.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Model, Q

from .crypto import PREFIX, CryptoError, current_key_id, needs_rotation, reencrypt


@dataclass
class RotationResult:
    rotated: int = 0          # values moved to the current key (or, in a dry run, that would be)
    unreadable: int = 0       # values that could not be read: left exactly as they were

    def __add__(self, other: RotationResult) -> RotationResult:
        return RotationResult(self.rotated + other.rotated, self.unreadable + other.unreadable)


# name -> function(batch_size, dry_run) that handles all the tenants for one module's tables
Rotator = Callable[[int, bool], RotationResult]
_rotators: dict[str, Rotator] = {}


def register(name: str, rotator: Rotator) -> None:
    """Called once at start-up by each module that stores encrypted values."""
    _rotators[name] = rotator


def rotators() -> dict[str, Rotator]:
    return dict(_rotators)


def _stale(field: str) -> Q:
    """A non-empty value that is not on the current key."""
    on_current = f"{PREFIX}:{current_key_id()}:"
    return Q(**{f"{field}__gt": ""}) & ~Q(**{f"{field}__startswith": on_current})


def rotate_model(
    model: type[Model], *, fields: dict[str, Callable[[Any], str]], tenant_id: uuid.UUID | str,
    scope: dict[str, Any], batch: int, dry_run: bool, select_related: tuple[str, ...] = (),
) -> RotationResult:
    """Re-encrypt `fields` (column name -> function giving that value's context for a row) of every
    row in `scope` that still uses an old key, `batch` rows at a time, in primary-key order."""
    stale = Q()
    for name in fields:
        stale |= _stale(name)
    result, last = RotationResult(), None
    while True:
        rows = model._default_manager.filter(stale, **scope)
        if select_related:
            rows = rows.select_related(*select_related)
        if last is not None:
            rows = rows.filter(pk__gt=last)
        chunk = list(rows.order_by("pk")[:batch])
        if not chunk:
            return result
        last = chunk[-1].pk
        with transaction.atomic():
            for row in chunk:
                changes: dict[str, str] = {}
                for name, context_of in fields.items():
                    token = getattr(row, name)
                    if not token or not needs_rotation(token):
                        continue
                    if dry_run:
                        result.rotated += 1
                        continue
                    try:
                        changes[name] = reencrypt(
                            token, tenant_id=tenant_id, context=context_of(row))
                    except CryptoError:
                        result.unreadable += 1
                if changes:
                    model._default_manager.filter(pk=row.pk).update(**changes)
                    result.rotated += len(changes)
