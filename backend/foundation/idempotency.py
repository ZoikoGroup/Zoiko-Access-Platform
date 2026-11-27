"""Idempotency: sending the same request twice has the same effect as sending it once.

    outcome = run(scope="actor:abc:POST /bookings", key=request_key, payload=body,
                  work=create_booking, retain_for=timedelta(days=1))

- First time: `work` runs and its small result is saved.
- Same key and same payload again: `work` is NOT run; the saved result is returned (replayed=True).
- Same key but a different payload: refused (DUPLICATE_COMMAND, HTTP 409). The key was misused.
- `work` raises: nothing is saved, so the client can retry with the same key.
- Two identical requests at the same moment: the second waits for the first, then replays.

Why this works: the key record is written in the SAME transaction as `work`. Other transactions
can only ever see it after the work has committed, and the database's unique index makes the
second request wait instead of racing.

The client's key and request are never stored, only their SHA-256 hashes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction

from .errors import ErrorCode, ZoikoError
from .models import IdempotencyKey
from .payloads import check_reference_payload
from .timeutil import Clock, utc_now

_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")  # GHE-13: 16-128 characters
_SCOPE = re.compile(r"^[A-Za-z0-9._:@/ -]{1,200}$")


@dataclass(frozen=True)
class IdempotentOutcome:
    replayed: bool
    result: dict[str, Any]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def request_digest(payload: Any) -> str:
    """Same content gives the same digest regardless of key order."""
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))


def run(
    *,
    scope: str,
    key: str,
    payload: Any,
    work: Callable[[], dict[str, Any]],
    retain_for: dt.timedelta,
    clock: Clock | None = None,
) -> IdempotentOutcome:
    """See the module docstring. `retain_for` has no default on purpose: pick it per operation."""
    if not _KEY.match(key):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["idempotency_key"])
    if not _SCOPE.match(scope):
        raise ValueError("invalid idempotency scope")
    key_hash = _sha256(key)
    digest = request_digest(payload)
    now = utc_now(clock)

    with transaction.atomic():
        try:
            with transaction.atomic():  # savepoint around the insert that may collide
                record = IdempotencyKey.objects.create(
                    scope=scope,
                    key_hash=key_hash,
                    request_digest=digest,
                    expires_at=now + retain_for,
                )
        except IntegrityError:
            existing = IdempotencyKey.objects.get(scope=scope, key_hash=key_hash)
            if existing.request_digest != digest:
                raise ZoikoError(ErrorCode.DUPLICATE_COMMAND) from None
            return IdempotentOutcome(replayed=True, result=existing.result)

        result = check_reference_payload(work())
        record.result = result
        record.completed_at = utc_now(clock)
        record.save(update_fields=["result", "completed_at"])
        return IdempotentOutcome(replayed=False, result=result)
