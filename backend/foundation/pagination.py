"""Cursor paging that cannot leak (GHE-13 T-07, DEV-171).

A cursor is only a position: the time and public id of the last row of the previous page. It is
signed, so it cannot be made up or edited, it is tied to the login that received it and to one
list, and it expires. It carries NO permission: every page is read again with the caller's
permission checked at that moment, so a right taken away between two pages stops the next page.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.core import signing
from django.db.models import Q, QuerySet

from foundation.errors import ErrorCode, ZoikoError

SALT = "zoiko.cursor.v1"
MAX_AGE_SECONDS = 900
DEFAULT_LIMIT, MAX_LIMIT = 50, 100


def limit_from(raw: object) -> int:
    if raw in (None, ""):
        return DEFAULT_LIMIT
    try:
        value = int(str(raw))
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["limit"]) from None
    if not 1 <= value <= MAX_LIMIT:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["limit"])
    return value


def make(*, actor: str, lst: str, created_at: datetime, public_id: str) -> str:
    return signing.dumps(
        {"a": actor, "l": lst, "t": created_at.isoformat(), "p": public_id},
        salt=SALT,
        compress=False,
    )


def read(token: str, *, actor: str, lst: str) -> tuple[datetime, str]:
    """The position in a cursor, or a validation error if it is forged, expired, or was given to
    someone else or for another list."""
    try:
        data = signing.loads(token, salt=SALT, max_age=MAX_AGE_SECONDS)
        if data["a"] != actor or data["l"] != lst:
            raise ValueError("not yours")
        return datetime.fromisoformat(data["t"]), str(data["p"])
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["cursor"]) from None


def page(
    rows: QuerySet[Any], *, limit: int, cursor: str | None, actor: str, lst: str
) -> tuple[list[Any], str | None]:
    """One page, newest first, and the cursor for the next (None at the end)."""
    rows = rows.order_by("-created_at", "-public_id")
    if cursor:
        at, pid = read(cursor, actor=actor, lst=lst)
        rows = rows.filter(Q(created_at__lt=at) | Q(created_at=at, public_id__lt=pid))
    found = list(rows[: limit + 1])
    more = len(found) > limit
    found = found[:limit]
    nxt = (
        make(actor=actor, lst=lst, created_at=found[-1].created_at, public_id=found[-1].public_id)
        if more
        else None
    )
    return found, nxt
