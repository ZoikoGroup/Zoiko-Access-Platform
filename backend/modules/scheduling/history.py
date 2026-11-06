"""The history of a booking: every step, kept for good and chained so tampering shows.

A step is written in the SAME transaction as the change it describes, so the history can never say
something happened that did not, nor miss something that did. Bookings made before this existed
have no history (nothing is invented for them).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now

from .models import Booking, BookingEvent, BookingEventSource

# 1 = the layout this history started with; 2 = the shared chain helper (foundation/chain.py).
DIGEST_VERSION = 2
PATIENT_SIDE_ROLES = frozenset({"R01", "R02"})          # patient or guardian
EVENT_TYPES = frozenset({
    "requested", "confirmed", "cancelled", "moved_away", "moved_here", "attended", "no_show",
    "reminder_sent",
})


def side_of(role_code: str) -> str:
    """Which side of the booking a login acted for, from the role it acted under."""
    return (BookingEventSource.PATIENT if role_code in PATIENT_SIDE_ROLES
            else BookingEventSource.CLINIC).value


def _parts(
    booking_ref: str, event_type: str, state_from: str, state_to: str, source: str, actor_ref: str,
    reason_code: str, related_ref: str, occurred_at_iso: str,
) -> list[str]:
    return [booking_ref, event_type, state_from, state_to, source, actor_ref, reason_code,
            related_ref, occurred_at_iso]


def _legacy_digest(
    prev: str, booking_ref: str, seq: int, event_type: str, state_from: str, state_to: str,
    source: str, actor_ref: str, reason_code: str, related_ref: str, occurred_at_iso: str,
) -> str:
    """Version 1: the layout rows were first written with. Kept only to check those old rows."""
    body = json.dumps(
        [prev, booking_ref, seq, event_type, state_from, state_to, source, actor_ref, reason_code,
         related_ref, occurred_at_iso], separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def log_event(
    booking: Booking,
    event_type: str,
    *,
    state_from: str,
    state_to: str,
    source: str,
    actor_ref: str,
    reason_code: str = "",
    related_ref: str = "",
    clock: Clock | None = None,
) -> None:
    """Add the next step to this booking's history. Call it inside the transaction of the change."""
    if event_type not in EVENT_TYPES or source not in BookingEventSource.values:
        raise ValueError("unknown booking event")
    ctx = require_context()
    scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
    last = BookingEvent.objects.filter(booking=booking, **scope).order_by("-seq").first()
    seq = last.seq + 1 if last else 1
    prev = last.record_digest if last else GENESIS
    now = utc_now(clock)
    digest = chain_digest(prev, seq, *_parts(
        booking.public_id, event_type, state_from, state_to, source, actor_ref, reason_code,
        related_ref, now.isoformat()))
    try:
        with transaction.atomic():
            BookingEvent.objects.create(
                booking=booking, seq=seq, event_type=event_type, state_from=state_from,
                state_to=state_to, source=source, actor_ref=actor_ref, reason_code=reason_code,
                related_ref=related_ref, occurred_at=now, digest_version=DIGEST_VERSION,
                prev_digest=prev, record_digest=digest)
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None    # another step got this place


def _expected_digest(booking_ref: str, prev: str, row: BookingEvent) -> str | None:
    """What this row's digest must be, by the layout it was written with (None: an unknown one)."""
    stamp = row.occurred_at.isoformat()
    if row.digest_version == 1:
        return _legacy_digest(prev, booking_ref, row.seq, row.event_type, row.state_from,
                              row.state_to, row.source, row.actor_ref, row.reason_code,
                              row.related_ref, stamp)
    if row.digest_version == DIGEST_VERSION:
        return chain_digest(prev, row.seq, *_parts(
            booking_ref, row.event_type, row.state_from, row.state_to, row.source, row.actor_ref,
            row.reason_code, row.related_ref, stamp))
    return None


def _verify(booking_ref: str, rows: list[BookingEvent]) -> tuple[bool, int | None]:
    return verify_chain(rows, digest_of=lambda prev, row: _expected_digest(booking_ref, prev, row))


def booking_history(booking_ref: str) -> dict[str, Any]:
    """The steps of one booking, oldest first, and whether the chain still checks out. The caller
    has authorized the read; the answer names who acted only by reference."""
    ctx = require_context()
    scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
    booking = Booking.objects.filter(public_id=booking_ref, **scope).first()
    if booking is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    rows = list(BookingEvent.objects.filter(booking=booking, **scope).order_by("seq"))
    ok, bad = _verify(booking.public_id, rows)
    return {
        "booking_ref": booking.public_id, "chain_ok": ok, "bad_seq": bad,
        "events": [
            {"seq": r.seq, "event_type": r.event_type, "state_from": r.state_from,
             "state_to": r.state_to, "source": r.source, "actor_ref": r.actor_ref,
             "reason_code": r.reason_code, "related_ref": r.related_ref,
             "occurred_at": r.occurred_at.isoformat()}
            for r in rows
        ],
    }
