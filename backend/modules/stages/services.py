"""Writing down and reading the stages a booking or a referral goes through (DEV-360).

`note_booking` / `note_referral` work out where the record stands now and add a step only when that
is new, so being called twice (an event delivered twice, an event and then a look) changes nothing.
They are called for every booking and referral event, and again whenever someone looks, which also
catches stages no event announces (a booking system going quiet, a referral passing its target).
"""
from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.lifecycles import BOOKING_STATES, REFERRAL_STATES, booking_state, referral_state
from foundation.timeutil import Clock, utc_now
from modules.bookingflow import api as bookingflow
from modules.referral import api as referral
from modules.scheduling import api as scheduling

from .models import StageStep, Subject

OBSERVED = "observed"


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _note(subject_type: str, subject_ref: str, state: str, cause: str,
          clock: Clock | None) -> bool:
    for _ in range(2):                          # a second writer at the same moment: read again
        with transaction.atomic():
            last = (StageStep.objects.filter(subject_type=subject_type, subject_ref=subject_ref,
                                             **_scope()).order_by("-seq").first())
            if last is not None and last.state == state:
                return False
            try:
                with transaction.atomic():
                    StageStep.objects.create(
                        subject_type=subject_type, subject_ref=subject_ref,
                        seq=(last.seq + 1) if last else 1, state=state, seen_at=utc_now(clock),
                        cause=cause[:64], **_scope())
                return True
            except IntegrityError:
                continue
    return False


def booking_state_now(booking_ref: str) -> str | None:
    try:
        booking = scheduling.get_booking(booking_ref)
    except ZoikoError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return None
        raise
    return booking_state(
        status=booking["status"], attendance=booking.get("attendance") or None,
        pending_change=booking.get("pending_change") or None,
        rescheduled_from=booking.get("rescheduled_from_ref") or None,
        provider_stage=bookingflow.stages([booking_ref]).get(booking_ref))


def note_booking(booking_ref: str, cause: str = OBSERVED, clock: Clock | None = None) -> bool:
    state = booking_state_now(booking_ref)
    return state is not None and _note(Subject.BOOKING, booking_ref, state, cause, clock)


def note_referral(referral_ref: str, cause: str = OBSERVED, clock: Clock | None = None) -> bool:
    try:
        found = referral.get_referral(referral_ref)
    except ZoikoError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return False
        raise
    state = referral_state(state=found["state"], acknowledged=bool(found["acknowledged_at"]),
                           escalated=bool(found["escalated"]))
    return _note(Subject.REFERRAL, referral_ref, state, cause, clock)


def steps(subject_type: str, subject_ref: str) -> list[dict[str, Any]]:
    """Every stored stage of one record, oldest first, in plain words."""
    labels = BOOKING_STATES if subject_type == Subject.BOOKING else REFERRAL_STATES
    rows = StageStep.objects.filter(
        subject_type=subject_type, subject_ref=subject_ref, **_scope()).order_by("seq")
    return [{"seq": r.seq, "state": r.state,
             "label": labels[r.state].label if r.state in labels else r.state,
             "at": r.seen_at.isoformat(), "by_event": r.cause != OBSERVED} for r in rows]
