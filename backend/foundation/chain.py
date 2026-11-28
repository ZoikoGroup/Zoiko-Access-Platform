"""A tamper-evident chain of rows: each row carries the digest of the one before it.

Used by the append-only histories (design: docs/schema README, standard column set B). Rows need
`seq`, `prev_digest` and `record_digest`; what else goes into a row's digest is up to the caller,
which passes the same list of parts when writing and when checking.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any, Protocol

GENESIS = "0" * 64          # the "previous digest" of the first row of a chain


class ChainRow(Protocol):
    seq: int
    prev_digest: str
    record_digest: str


def chain_digest(prev: str, seq: int, *parts: Any) -> str:
    """The digest of one row: the previous digest, its place and its own content."""
    body = json.dumps([prev, seq, *parts], separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def verify_chain(
    rows: Sequence[Any],
    parts_of: Callable[[Any], Sequence[Any]] | None = None,
    *,
    digest_of: Callable[[str, Any], str | None] | None = None,
) -> tuple[bool, int | None]:
    """Check rows (oldest first) from the start. Returns (True, None), or (False, the place of the
    first row that does not fit). A chain cut off at the NEWEST end cannot be seen from here.

    Normally a row's digest is `chain_digest(previous, seq, *parts_of(row))`. A history that changed
    how it makes digests over time passes `digest_of(previous digest, row)` instead, which returns
    what the row's digest must be (or None for a row it does not know how to check: that fails)."""
    if (parts_of is None) == (digest_of is None):
        raise ValueError("pass exactly one of parts_of and digest_of")
    prev = GENESIS
    for expected, row in enumerate(rows, start=1):
        want = (digest_of(prev, row) if digest_of is not None
                else chain_digest(prev, row.seq, *parts_of(row)))   # type: ignore[misc]
        good = (row.seq == expected and row.prev_digest == prev and want is not None
                and row.record_digest == want)
        if not good:
            return False, expected
        prev = row.record_digest
    return True, None
