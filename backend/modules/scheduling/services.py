"""Creating slots, booking, confirming and cancelling.

Access: booking, confirming and cancelling need a policy RECEIPT, an ALLOW decision for exactly
this actor, operation, organization and patient (see policy.check_receipt). Callers get one
from policy.authorize(), which records denials. The domain therefore enforces access itself.

Every change is audited and emits an outbox event in the same transaction.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.db.models.functions import TruncDate

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.stats import summarize
from foundation.timeutil import Clock, TimeError, from_local, get_zone, utc_now
from modules.catalog import api as catalog
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .history import PATIENT_SIDE_ROLES, log_event, side_of
from .models import (
    ACTIVE_BOOKING,
    CLINIC_CANCEL_REASONS,
    PATIENT_CANCEL_REASONS,
    Attendance,
    Booking,
    BookingStatus,
    Slot,
    SlotStatus,
)

WRITE_BOOKING = "write:booking"
MAX_REPEAT_DAYS = 31          # one request fills at most a month of days
MAX_NEW_SLOTS = 400           # and at most this many times in all
# Only organization staff may confirm. (Patients and guardians may book and cancel.)
CONFIRMING_ROLES = frozenset({"R04", "R13"})


def _audit(by: str, action: str, object_type: str, object_ref: str) -> None:
    record(actor_ref=by, action=action, object_type=object_type, object_ref=object_ref,
           outcome=AuditOutcome.ALLOWED)


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _event(
    event_type: str, booking: Booking, *, actor_ref: str = "", side: str = ""
) -> None:
    """Which booking changed, and who acted: the side (patient or clinic) and, for clinic staff
    only, their login. A patient is never named: only "patient"."""
    payload = {
        "booking_ref": booking.public_id, "slot_ref": booking.slot.public_id,
        "service_ref": booking.slot.service_ref, "status": booking.status,
    }
    if side:
        payload["side"] = side
    if actor_ref and side != "patient":
        payload["actor_ref"] = actor_ref
    emit(event_type=event_type, aggregate_type="booking", aggregate_ref=booking.public_id,
         payload=payload)


def _times_opened(service_ref: str) -> None:
    """Say that a time of this service may now be free (new times, or a booking was freed). The
    waiting list listens; the event holds only the service id."""
    emit(event_type="slot.opened.v1", aggregate_type="service", aggregate_ref=service_ref,
         payload={"service_ref": service_ref})


# ------------------------------------------------------------------ slots
def create_slots(
    *,
    service_ref: str,
    location_ref: str,
    first_local: dt.datetime,
    count: int,
    actor_public_id: str,
    decision: policy.Decision,
    step_minutes: int | None = None,
    provider_ref: str = "",
    days: int = 1,
    weekdays_only: bool = False,
    clock: Clock | None = None,
) -> list[dict[str, Any]]:
    """Add `count` back-to-back slots starting at a wall-clock time at the location, and the same
    for each of the next days (`days` in all; with `weekdays_only` Saturdays and Sundays are
    skipped and do not use up a day).

    `first_local` is NAIVE: the number on the clinic's clock. Times that do not exist (the
    spring-forward gap) or happen twice (fall-back) are refused rather than guessed (DEV-017).
    All slots are created or none, whatever the number of days.
    """
    service = catalog.get_service(service_ref)
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation="write:catalog",
        organization_public_id=service["organization_ref"], subject_ref=None,
    )
    if not 1 <= count <= 200:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["count"])
    if not 1 <= days <= MAX_REPEAT_DAYS or count * days > MAX_NEW_SLOTS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["days"])
    location = catalog.get_location(location_ref)
    if service["status"] != "active" or location["status"] != "active":
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["service"])
    if service["organization_ref"] != location["organization_ref"]:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["location"])
    if provider_ref and not trust.is_clinician_at(provider_ref, service["organization_ref"]):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["provider"])   # must be a doctor here
    step = step_minutes or int(service["duration_minutes"])
    if not 5 <= step <= 480:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["step_minutes"])
    duration = dt.timedelta(minutes=int(service["duration_minutes"]))
    now = utc_now(clock)
    created: list[Slot] = []
    # The days to fill: `days` of them, counting from the first, leaving out weekends if asked.
    day_starts: list[dt.datetime] = []
    cursor = first_local
    while len(day_starts) < days:
        if not (weekdays_only and cursor.weekday() >= 5):
            day_starts.append(cursor)
        cursor += dt.timedelta(days=1)
    with transaction.atomic():
        for day_start in day_starts:
            for i in range(count):
                try:
                    start = from_local(
                        day_start + dt.timedelta(minutes=i * step), location["time_zone"]
                    ).instant
                except TimeError:
                    raise ZoikoError(
                        ErrorCode.VALIDATION_FAILED, fields=["first_local"]) from None
                if start <= now:
                    raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["first_local"])
                earlier = Slot.objects.filter(
                    service_ref=service_ref, location_ref=location_ref, starts_at=start,
                    provider_ref=provider_ref, status=SlotStatus.WITHDRAWN, **_scope()).first()
                if earlier is not None:                 # taken off the calendar before: put it back
                    earlier.status, earlier.ends_at = SlotStatus.OPEN, start + duration
                    earlier.entity_version += 1
                    earlier.save(update_fields=[
                        "status", "ends_at", "entity_version", "updated_at"])
                    created.append(earlier)
                    continue
                try:
                    with transaction.atomic():
                        created.append(Slot.objects.create(
                            service_ref=service_ref, location_ref=location_ref,
                            organization_ref=service["organization_ref"], starts_at=start,
                            ends_at=start + duration, local_zone=location["time_zone"],
                            provider_ref=provider_ref,
                        ))
                except IntegrityError:
                    raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None
        _audit(actor_public_id, "slot.create", "service", service_ref)
        _times_opened(service_ref)
        return [slot_dict(s) for s in created]


MAX_WITHDRAW_SPAN_DAYS = 62


def withdraw_slot(
    *,
    slot_ref: str,
    actor_public_id: str,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> None:
    """Take ONE free, future time off the calendar. A time that has a booking (waiting or
    confirmed), is already withdrawn, or has already started is refused and nothing changes.
    The time can be put back by creating it again."""
    slot = Slot.objects.select_for_update().filter(public_id=slot_ref, **_scope()).first()
    if slot is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation="write:catalog",
        organization_public_id=slot.organization_ref, subject_ref=None,
    )
    taken = Booking.objects.filter(slot=slot, status__in=ACTIVE_BOOKING).exists()
    if slot.status != SlotStatus.OPEN or slot.starts_at <= utc_now(clock) or taken:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION)
    slot.status = SlotStatus.WITHDRAWN
    slot.entity_version += 1
    slot.save(update_fields=["status", "entity_version", "updated_at"])
    _audit(actor_public_id, "slot.withdraw_one", "slot", slot_ref)


def withdraw_slots(
    *,
    service_ref: str,
    first_day: dt.date,
    last_day: dt.date,
    actor_public_id: str,
    decision: policy.Decision,
    provider_ref: str = "",
    dry_run: bool = False,
    clock: Clock | None = None,
) -> int:
    """Take the FREE, FUTURE times of a service off the calendar for these clinic days (both ends
    included, by each time's own clinic clock), for one doctor if given. A time that has a booking,
    waiting or confirmed, is NEVER touched: those appointments are cancelled or moved one by one.
    With `dry_run` it only counts. Returns how many times were (or would be) taken off.

    A withdrawn time can be put back by creating the same time again."""
    service = catalog.get_service(service_ref)
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation="write:catalog",
        organization_public_id=service["organization_ref"], subject_ref=None,
    )
    if last_day < first_day or (last_day - first_day).days >= MAX_WITHDRAW_SPAN_DAYS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["last_day"])
    now = utc_now(clock)
    taken = Booking.objects.filter(slot=OuterRef("pk"), status__in=ACTIVE_BOOKING)
    rows = (
        Slot.objects.filter(
            service_ref=service_ref, status=SlotStatus.OPEN, starts_at__gt=now,
            # a day either side of the range, then the exact clinic day is checked per time
            starts_at__gte=dt.datetime.combine(first_day, dt.time.min, dt.timezone.utc)
            - dt.timedelta(days=1),
            starts_at__lt=dt.datetime.combine(last_day, dt.time.min, dt.timezone.utc)
            + dt.timedelta(days=2),
            **_scope())
        .annotate(is_taken=Exists(taken)).filter(is_taken=False)
    )
    if provider_ref:
        rows = rows.filter(provider_ref=provider_ref)
    chosen = [s for s in rows if first_day <= s.starts_at.astimezone(get_zone(s.local_zone)).date()
              <= last_day]
    if dry_run or not chosen:
        return len(chosen)
    with transaction.atomic():
        Slot.objects.filter(pk__in=[s.pk for s in chosen]).update(
            status=SlotStatus.WITHDRAWN, updated_at=now)
        _audit(actor_public_id, "slot.withdraw", "service", service_ref)
    return len(chosen)


def _open_slots(service_ref: str, now: dt.datetime) -> QuerySet[Slot]:
    taken = Booking.objects.filter(slot=OuterRef("pk"), status__in=ACTIVE_BOOKING)
    return (
        Slot.objects.filter(
            service_ref=service_ref, status=SlotStatus.OPEN, starts_at__gt=now, **_scope())
        .annotate(is_taken=Exists(taken))
        .filter(is_taken=False)
    )


def list_open_slots(
    service_ref: str, *, until: dt.datetime | None = None, limit: int = 60,
    clock: Clock | None = None,
) -> list[dict[str, Any]]:
    rows = _open_slots(service_ref, utc_now(clock))
    if until is not None:
        rows = rows.filter(starts_at__lt=until)
    return [slot_dict(s) for s in rows.order_by("starts_at")[: max(1, min(limit, 200))]]


def slot_dict(slot: Slot) -> dict[str, Any]:
    local = slot.starts_at.astimezone(get_zone(slot.local_zone))
    return {
        "public_id": slot.public_id, "service_ref": slot.service_ref,
        "location_ref": slot.location_ref, "organization_ref": slot.organization_ref,
        "starts_at": slot.starts_at.isoformat(), "ends_at": slot.ends_at.isoformat(),
        "local_zone": slot.local_zone, "local_start": local.replace(tzinfo=None).isoformat(),
        "status": slot.status, "provider_ref": slot.provider_ref,
    }


# ------------------------------------------------------------------ bookings
def booking_dict(booking: Booking) -> dict[str, Any]:
    return {
        "public_id": booking.public_id, "status": booking.status,
        "person_ref": booking.person_ref, "booked_by_ref": booking.booked_by_ref,
        "created_at": booking.created_at.isoformat(), "slot": slot_dict(booking.slot),
        "rescheduled_from_ref": booking.rescheduled_from_ref,
        "attendance": booking.attendance,
        "cancel_reason": booking.cancel_reason,
        "pending_change": booking.pending_change,
        "attendance_at": booking.attendance_at.isoformat() if booking.attendance_at else None,
    }


def _own_booking(booking_ref: str) -> Booking:
    row = Booking.objects.select_related("slot").filter(
        public_id=booking_ref, **_scope()).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


def book(
    *,
    slot_ref: str,
    person_ref: str,
    actor_public_id: str,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Request a booking on an open slot for a patient. Needs a policy receipt."""
    with transaction.atomic():
        # Lock the slot row so two people booking it at once are taken one after the other.
        slot = Slot.objects.select_for_update().filter(public_id=slot_ref, **_scope()).first()
        if slot is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_BOOKING,
            organization_public_id=slot.organization_ref, subject_ref=person_ref,
        )
        if slot.status != SlotStatus.OPEN or slot.starts_at <= utc_now(clock):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["slot"])
        if not catalog.is_offered(catalog.get_service(slot.service_ref)):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["slot"])   # not published yet
        try:
            with transaction.atomic():
                booking = Booking.objects.create(
                    slot=slot, person_ref=person_ref, booked_by_ref=actor_public_id)
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None  # someone else got it
        trust.link_person_to_site(person_ref, slot.organization_ref, "booking")
        _audit(actor_public_id, "booking.request", "booking", booking.public_id)
        _event("booking.requested.v1", booking, actor_ref=actor_public_id,
               side=side_of(decision.role_code))
        log_event(booking, "requested", state_from="", state_to=booking.status,
                  source=side_of(decision.role_code), actor_ref=actor_public_id, clock=clock)
        return booking_dict(booking)


def confirm(
    *, booking_ref: str, actor_public_id: str, decision: policy.Decision
) -> dict[str, Any]:
    """The organization accepts a requested booking."""
    with transaction.atomic():
        booking = _own_booking(booking_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_BOOKING,
            organization_public_id=booking.slot.organization_ref, subject_ref=booking.person_ref,
        )
        if decision.role_code not in CONFIRMING_ROLES:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if booking.status == BookingStatus.CONFIRMED:
            return booking_dict(booking)                    # repeating is harmless
        if booking.status != BookingStatus.REQUESTED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = utc_now()
        booking.entity_version += 1
        booking.save(update_fields=["status", "confirmed_at", "entity_version", "updated_at"])
        _audit(actor_public_id, "booking.confirm", "booking", booking.public_id)
        _link_doctor(booking, actor_public_id)
        _event("booking.confirmed.v1", booking, actor_ref=actor_public_id,
               side=side_of(decision.role_code))
        log_event(booking, "confirmed", state_from=BookingStatus.REQUESTED,
                  state_to=booking.status, source=side_of(decision.role_code),
                  actor_ref=actor_public_id)
        _finish_move(booking, actor_public_id, side_of(decision.role_code))
        return booking_dict(booking)


CARE_LINK_AFTER_VISIT = dt.timedelta(days=30)


def _link_doctor(booking: Booking, confirmed_by: str) -> None:
    """A confirmed appointment gives the doctor a care relationship with the patient (the link
    the access rules require before a doctor may see them). It is tied to the clinic site
    (it counts only while the doctor holds a care role there) and runs until 30 days after the
    visit (30 is our own number, DEV-106). If the doctor can no longer hold one, the booking is
    still confirmed."""
    slot = booking.slot
    if not slot.provider_ref:
        return
    try:
        with transaction.atomic():
            trust.ensure_care_relationship(
                actor_public_id=slot.provider_ref, person_public_id=booking.person_ref,
                basis=trust.CareBasis.APPOINTMENT,
                valid_until=slot.ends_at + CARE_LINK_AFTER_VISIT,
                organization_ref=slot.organization_ref, by=confirmed_by)
    except ZoikoError:
        return


def _check_reason(reason: str, role_code: str) -> str:
    """A cancellation reason is optional. If given it must be one of the codes for the side the
    person is on (patient or guardian, or clinic staff)."""
    reason = (reason or "").strip()
    allowed = (PATIENT_CANCEL_REASONS if role_code in PATIENT_SIDE_ROLES else CLINIC_CANCEL_REASONS)
    if reason and reason not in allowed:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["reason"])
    return reason


def _origin_waiting(booking: Booking) -> Booking | None:
    """The confirmed appointment this new request is meant to replace, if it still waits."""
    if not booking.rescheduled_from_ref:
        return None
    origin = Booking.objects.select_related("slot").filter(
        public_id=booking.rescheduled_from_ref, **_scope()).first()
    if origin and origin.status == BookingStatus.CONFIRMED and origin.pending_change == "rebook":
        return origin
    return None


def _restore_origin(booking: Booking, actor_ref: str, side: str) -> None:
    """A requested move was cancelled or turned down: the original appointment is untouched and
    simply stops waiting (T-27)."""
    origin = _origin_waiting(booking)
    if origin is None:
        return
    origin.pending_change = ""
    origin.entity_version += 1
    origin.save(update_fields=["pending_change", "entity_version", "updated_at"])
    log_event(origin, "change_withdrawn", state_from=origin.status, state_to=origin.status,
              source=side, actor_ref=actor_ref, related_ref=booking.public_id)


def _finish_move(booking: Booking, actor_ref: str, side: str) -> None:
    """The clinic confirmed the new time: only now is the original appointment let go (WF-13)."""
    origin = _origin_waiting(booking)
    if origin is None:
        return
    origin.status = BookingStatus.CANCELLED
    origin.pending_change = ""
    origin.cancelled_by_ref = actor_ref
    origin.entity_version += 1
    origin.save(update_fields=["status", "pending_change", "cancelled_by_ref",
                               "entity_version", "updated_at"])
    log_event(origin, "moved_away", state_from=BookingStatus.CONFIRMED, state_to=origin.status,
              source=side, actor_ref=actor_ref, related_ref=booking.public_id)
    log_event(booking, "moved_here", state_from=BookingStatus.CONFIRMED,
              state_to=booking.status, source=side, actor_ref=actor_ref,
              related_ref=origin.public_id)
    if origin.slot.starts_at > utc_now():
        _times_opened(origin.slot.service_ref)                 # the old time is free again


def cancel(
    *, booking_ref: str, actor_public_id: str, decision: policy.Decision, reason: str = ""
) -> dict[str, Any]:
    """Cancel a booking, with an optional reason. The slot becomes bookable again. Cancelling a
    booking that is already cancelled changes nothing (the first reason is kept)."""
    with transaction.atomic():
        booking = _own_booking(booking_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_BOOKING,
            organization_public_id=booking.slot.organization_ref, subject_ref=booking.person_ref,
        )
        if booking.status == BookingStatus.CANCELLED:
            return booking_dict(booking)
        clean_reason = _check_reason(reason, decision.role_code)
        if (decision.role_code in PATIENT_SIDE_ROLES and booking.status == BookingStatus.CONFIRMED
                and booking.slot.starts_at > utc_now()):
            # The patient asks; the appointment stays until the clinic answers (T-26).
            if booking.pending_change == "cancel":
                return booking_dict(booking)                # asked already
            if booking.pending_change:
                raise ZoikoError(ErrorCode.CONFLICT_VERSION)    # a move is waiting for the clinic
            booking.pending_change = "cancel"
            booking.cancel_reason = clean_reason
            booking.entity_version += 1
            booking.save(update_fields=["pending_change", "cancel_reason", "entity_version",
                                        "updated_at"])
            _audit(actor_public_id, "booking.cancel_request", "booking", booking.public_id)
            log_event(booking, "cancel_requested", state_from=booking.status,
                      state_to=booking.status, source=side_of(decision.role_code),
                      actor_ref=actor_public_id, reason_code=clean_reason)
            return booking_dict(booking)
        # The clinic answers (or cancels by itself), or there is nothing confirmed to protect.
        booking.cancel_reason = clean_reason or (
            booking.cancel_reason if booking.pending_change == "cancel" else "")
        was = booking.status
        booking.status = BookingStatus.CANCELLED
        booking.pending_change = ""
        booking.cancelled_by_ref = actor_public_id
        booking.entity_version += 1
        booking.save(update_fields=["status", "cancelled_by_ref", "cancel_reason",
                                    "pending_change", "entity_version", "updated_at"])
        _audit(actor_public_id, "booking.cancel", "booking", booking.public_id)
        _event("booking.cancelled.v1", booking, actor_ref=actor_public_id,
               side=side_of(decision.role_code))
        log_event(booking, "cancelled", state_from=was, state_to=booking.status,
                  source=side_of(decision.role_code), actor_ref=actor_public_id,
                  reason_code=booking.cancel_reason)
        if was == BookingStatus.REQUESTED:
            _restore_origin(booking, actor_public_id, side_of(decision.role_code))
        if booking.slot.starts_at > utc_now():
            _times_opened(booking.slot.service_ref)             # the time is free again
        return booking_dict(booking)


def _ask_to_move(
    old: Booking, slot: Slot, actor_ref: str, decision: policy.Decision, clock: Clock | None
) -> dict[str, Any]:
    """The patient asks for a new time. The original stays confirmed until the clinic answers."""
    side = side_of(decision.role_code)
    try:
        with transaction.atomic():
            new = Booking.objects.create(
                slot=slot, person_ref=old.person_ref, booked_by_ref=actor_ref,
                rescheduled_from_ref=old.public_id, status=BookingStatus.REQUESTED)
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None      # someone took that time
    old.pending_change = "rebook"
    old.entity_version += 1
    old.save(update_fields=["pending_change", "entity_version", "updated_at"])
    _audit(actor_ref, "booking.reschedule_request", "booking", new.public_id)
    _event("booking.rescheduled.v1", new, actor_ref=actor_ref, side=side)
    log_event(old, "change_requested", state_from=old.status, state_to=old.status, source=side,
              actor_ref=actor_ref, related_ref=new.public_id, clock=clock)
    log_event(new, "requested", state_from="", state_to=new.status, source=side,
              actor_ref=actor_ref, related_ref=old.public_id, clock=clock)
    return booking_dict(new)


def reschedule(
    *,
    booking_ref: str,
    new_slot_ref: str,
    actor_public_id: str,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Move an appointment to another free time of the SAME service at the SAME organization.

    A patient moving a CONFIRMED appointment only asks: the original stays confirmed, and keeps
    its time, until the clinic confirms the new one (then it is let go); if the clinic turns the
    new time down, or the patient withdraws it, nothing changed (GHE-14 WF-13, T-27). Otherwise
    (a waiting request, or the clinic's own move) the old booking is cancelled and a new one
    made in one step; if the new time was just taken, nothing changes. A move made by clinic
    staff of a confirmed appointment stays confirmed."""
    now = utc_now(clock)
    with transaction.atomic():
        old = _own_booking(booking_ref)
        slot = Slot.objects.select_for_update().filter(
            public_id=new_slot_ref, **_scope()).first()
        if slot is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_BOOKING,
            organization_public_id=old.slot.organization_ref, subject_ref=old.person_ref)
        if old.status not in ACTIVE_BOOKING or old.slot.starts_at <= now:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)       # cancelled, or already happened
        if (slot.pk == old.slot_id or slot.service_ref != old.slot.service_ref
                or slot.organization_ref != old.slot.organization_ref
                or slot.status != SlotStatus.OPEN or slot.starts_at <= now):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["slot"])
        if decision.role_code in PATIENT_SIDE_ROLES and old.pending_change:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)       # a change already waits
        if decision.role_code in PATIENT_SIDE_ROLES and old.status == BookingStatus.CONFIRMED:
            return _ask_to_move(old, slot, actor_public_id, decision, clock)
        keep_confirmed = (old.status == BookingStatus.CONFIRMED
                          and decision.role_code in CONFIRMING_ROLES)
        # A new request that replaces an earlier request keeps pointing at the original
        # appointment that is still waiting to be replaced.
        origin = _origin_waiting(old) if old.status == BookingStatus.REQUESTED else None
        was = old.status
        old.status = BookingStatus.CANCELLED
        old.pending_change = ""
        old.cancelled_by_ref = actor_public_id
        old.entity_version += 1
        old.save(update_fields=["status", "pending_change", "cancelled_by_ref",
                                "entity_version", "updated_at"])
        try:
            with transaction.atomic():
                new = Booking.objects.create(
                    slot=slot, person_ref=old.person_ref, booked_by_ref=actor_public_id,
                    rescheduled_from_ref=origin.public_id if origin else old.public_id,
                    status=BookingStatus.CONFIRMED if keep_confirmed else BookingStatus.REQUESTED)
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None  # someone took it: undo all
        _audit(actor_public_id, "booking.reschedule", "booking", new.public_id)
        if new.status == BookingStatus.CONFIRMED:
            _link_doctor(new, actor_public_id)
        _event("booking.rescheduled.v1", new, actor_ref=actor_public_id,
               side=side_of(decision.role_code))
        side = side_of(decision.role_code)
        log_event(old, "moved_away", state_from=was, state_to=old.status, source=side,
                  actor_ref=actor_public_id, related_ref=new.public_id, clock=clock)
        log_event(new, "moved_here", state_from="", state_to=new.status, source=side,
                  actor_ref=actor_public_id, related_ref=old.public_id, clock=clock)
        _times_opened(old.slot.service_ref)                    # the old time is free again
        return booking_dict(new)


def mark_attendance(
    *,
    booking_ref: str,
    outcome: str,
    actor_public_id: str,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """The clinic records whether the patient came to a CONFIRMED appointment that has started.

    Only clinic operations staff may do it. It can be corrected later (the audit chain keeps the
    first answer). Saying the same thing twice changes nothing."""
    now = utc_now(clock)
    with transaction.atomic():
        booking = _own_booking(booking_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_BOOKING,
            organization_public_id=booking.slot.organization_ref, subject_ref=booking.person_ref)
        if decision.role_code not in CONFIRMING_ROLES:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if outcome not in Attendance.values:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["outcome"])
        if booking.status != BookingStatus.CONFIRMED or booking.slot.starts_at > now:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)   # not confirmed, or not started yet
        if booking.attendance == outcome:
            return booking_dict(booking)                   # repeating is harmless
        booking.attendance = outcome
        booking.attendance_by_ref = actor_public_id
        booking.attendance_at = now
        booking.entity_version += 1
        booking.save(update_fields=["attendance", "attendance_by_ref", "attendance_at",
                                    "entity_version", "updated_at"])
        _audit(actor_public_id, f"booking.{outcome}", "booking", booking.public_id)
        log_event(booking, outcome, state_from=booking.status, state_to=booking.status,
                  source=side_of(decision.role_code), actor_ref=actor_public_id, clock=clock)
        emit(event_type="booking.attendance.v1", aggregate_type="booking",
             aggregate_ref=booking.public_id,
             payload={"booking_ref": booking.public_id, "status": booking.status,
                      "attendance": booking.attendance})
        return booking_dict(booking)


def send_due_reminders(
    *, lead: dt.timedelta | None = None, limit: int = 200, clock: Clock | None = None
) -> int:
    """Remind patients of CONFIRMED appointments that start within `lead` and were not reminded yet.

    Runs for the bound tenant inside a transaction. Each booking is reminded once: the mark and the
    event are written together, and rows another worker holds are skipped, so two workers never
    send a second reminder. Returns how many were reminded."""
    now = utc_now(clock)
    window = lead if lead is not None else dt.timedelta(hours=settings.REMINDER_LEAD_HOURS)
    with transaction.atomic():
        due = list(
            Booking.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("slot")
            .filter(status=BookingStatus.CONFIRMED, reminder_sent_at__isnull=True,
                    slot__starts_at__gt=now, slot__starts_at__lte=now + window, **_scope())
            .order_by("slot__starts_at")[: max(1, min(limit, 1000))])
        for booking in due:
            booking.reminder_sent_at = now
            booking.entity_version += 1
            booking.save(update_fields=["reminder_sent_at", "entity_version", "updated_at"])
            _audit("system", "booking.reminder", "booking", booking.public_id)
            _event("booking.reminder.v1", booking)
            log_event(booking, "reminder_sent", state_from=booking.status,
                      state_to=booking.status, source="system", actor_ref="system", clock=clock)
        return len(due)


def get_slot(slot_ref: str) -> dict[str, Any]:
    slot = Slot.objects.filter(public_id=slot_ref, **_scope()).first()
    if slot is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return slot_dict(slot)


def list_bookings_for_provider(provider_ref: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Confirmed bookings on this doctor's times, oldest first. The caller checks access."""
    rows = Booking.objects.select_related("slot").filter(
        slot__provider_ref=provider_ref, status=BookingStatus.CONFIRMED, **_scope())
    return [booking_dict(b) for b in rows.order_by("slot__starts_at")[: max(1, min(limit, 500))]]


def get_booking(booking_ref: str) -> dict[str, Any]:
    """One booking as a dict. Callers must already be authorized to see it."""
    return booking_dict(_own_booking(booking_ref))


def list_bookings(
    *, person_refs: list[str] | None = None, organization_ref: str = "", limit: int = 100
) -> list[dict[str, Any]]:
    """Bookings for these patients and/or this organization. The caller has authorized each."""
    rows = Booking.objects.select_related("slot").filter(**_scope())
    if person_refs is not None and organization_ref:
        rows = rows.filter(
            Q(person_ref__in=person_refs) | Q(slot__organization_ref=organization_ref))
    elif person_refs is not None:
        rows = rows.filter(person_ref__in=person_refs)
    elif organization_ref:
        rows = rows.filter(slot__organization_ref=organization_ref)
    else:
        return []
    return [booking_dict(b) for b in rows.order_by("slot__starts_at")[: max(1, min(limit, 200))]]


def booking_counts(clock: Clock | None = None) -> dict[str, int]:
    """Bookings by status, and how many future slots are still free. Numbers only."""
    scope = _scope()
    rows = Booking.objects.filter(**scope)
    return {
        "requested": rows.filter(status=BookingStatus.REQUESTED).count(),
        "confirmed": rows.filter(status=BookingStatus.CONFIRMED).count(),
        "cancelled": rows.filter(status=BookingStatus.CANCELLED).count(),
        "open_slots": sum(
            _open_slots(ref, utc_now(clock)).count()
            for ref in Slot.objects.filter(**scope).values_list("service_ref", flat=True).distinct()
        ),
    }


def today_counts(organization_ref: str, clock: Clock | None = None) -> dict[str, int]:
    """Appointments of this organization on today's clinic day (each by its own location's clock):
    confirmed ones, requests still waiting, and how many of those have not started yet.
    Numbers only, no patient."""
    now = utc_now(clock)
    rows = Booking.objects.select_related("slot").filter(
        slot__organization_ref=organization_ref, status__in=ACTIVE_BOOKING,
        slot__starts_at__gt=now - dt.timedelta(days=2),
        slot__starts_at__lt=now + dt.timedelta(days=2), **_scope())
    out = {"confirmed": 0, "waiting": 0, "still_to_come": 0}
    for b in rows:
        zone = get_zone(b.slot.local_zone)
        if b.slot.starts_at.astimezone(zone).date() != now.astimezone(zone).date():
            continue
        out["confirmed" if b.status == BookingStatus.CONFIRMED else "waiting"] += 1
        if b.slot.starts_at > now:
            out["still_to_come"] += 1
    return out


def provider_load(
    organization_ref: str, provider_refs: list[str], days: int = 14, clock: Clock | None = None
) -> dict[str, dict[str, Any]]:
    """For each doctor at an organization: confirmed appointments and free times in the next
    `days` days, and the start of the next appointment. Numbers and times only, no patient."""
    now = utc_now(clock)
    until = now + dt.timedelta(days=days)
    out: dict[str, dict[str, Any]] = {
        ref: {"upcoming": 0, "open_times": 0, "next_local": None, "next_zone": None}
        for ref in provider_refs}
    booked = Booking.objects.select_related("slot").filter(
        slot__organization_ref=organization_ref, slot__provider_ref__in=provider_refs,
        status=BookingStatus.CONFIRMED, slot__starts_at__gt=now, slot__starts_at__lt=until,
        **_scope()).order_by("slot__starts_at")
    for b in booked:
        row = out[b.slot.provider_ref]
        row["upcoming"] += 1
        if row["next_local"] is None:
            row["next_local"] = slot_dict(b.slot)["local_start"]
            row["next_zone"] = b.slot.local_zone
    taken = Booking.objects.filter(slot=OuterRef("pk"), status__in=ACTIVE_BOOKING)
    free = (
        Slot.objects.filter(
            organization_ref=organization_ref, provider_ref__in=provider_refs,
            status=SlotStatus.OPEN, starts_at__gt=now, starts_at__lt=until, **_scope())
        .annotate(is_taken=Exists(taken)).filter(is_taken=False)
        .values("provider_ref").annotate(n=Count("id")))
    for row in free:
        out[row["provider_ref"]]["open_times"] = row["n"]
    return out


def usual_confirmation_hours(
    days: int = 90, minimum: int = 5, clock: Clock | None = None
) -> dict[str, float]:
    """Per organization, the middle number of hours it took to confirm bookings made in the last
    `days` days. An organization with fewer than `minimum` confirmations is left out, so a handful
    of bookings can never be traced back to one patient. Numbers only."""
    since = utc_now(clock) - dt.timedelta(days=days)
    waits: dict[str, list[float]] = {}
    for org, created, confirmed in Booking.objects.filter(
        confirmed_at__isnull=False, created_at__gte=since, **_scope()
    ).values_list("slot__organization_ref", "created_at", "confirmed_at")[:20000]:
        if confirmed is not None:
            waits.setdefault(org, []).append((confirmed - created).total_seconds() / 3600)
    return {
        org: float(summarize(values)["median"] or 0.0)
        for org, values in waits.items() if len(values) >= minimum
    }


def waiting_requests(organization_ref: str, limit: int = 500) -> list[tuple[str, dt.datetime]]:
    """(booking id, when it was made) of the bookings of this organization still waiting for
    confirmation, oldest first."""
    rows = Booking.objects.filter(
        status=BookingStatus.REQUESTED, slot__organization_ref=organization_ref, **_scope()
    ).order_by("created_at").values_list("public_id", "created_at")
    return list(rows[: max(1, min(limit, 1000))])


def overdue_requests(
    hours: int, clock: Clock | None = None, organization_ref: str = ""
) -> int:
    """Requested bookings that have waited for confirmation for more than `hours` hours, for one
    organization when given, else for the whole customer."""
    cutoff = utc_now(clock) - dt.timedelta(hours=hours)
    rows = Booking.objects.filter(
        status=BookingStatus.REQUESTED, created_at__lt=cutoff, **_scope())
    if organization_ref:
        rows = rows.filter(slot__organization_ref=organization_ref)
    return rows.count()


def _rate(missed: int, attended: int) -> float | None:
    """Share of recorded visits that were missed, 0 to 1; None when no visit was recorded yet."""
    recorded = missed + attended
    return round(missed / recorded, 3) if recorded else None


def report(
    organization_ref: str, since: dt.datetime, until: dt.datetime
) -> dict[str, Any]:
    """Counts only, for one organization over a period: bookings made per day (UTC dates), their
    status now, how many past confirmed appointments were attended, missed or not yet recorded,
    and the busiest services. No patient is identified anywhere in the result."""
    scope = _scope()
    made = Booking.objects.filter(
        slot__organization_ref=organization_ref, created_at__gte=since, created_at__lt=until,
        **scope)
    per_day = {
        row["day"]: row["n"]
        for row in made.annotate(day=TruncDate("created_at")).values("day").annotate(n=Count("id"))
    }
    days: list[dict[str, Any]] = []
    cursor = since.date()
    while cursor <= (until - dt.timedelta(seconds=1)).date():
        days.append({"date": cursor.isoformat(), "count": per_day.get(cursor, 0)})
        cursor += dt.timedelta(days=1)
    status = {row["status"]: row["n"] for row in made.values("status").annotate(n=Count("id"))}
    held = Booking.objects.filter(
        slot__organization_ref=organization_ref, status=BookingStatus.CONFIRMED,
        slot__starts_at__gte=since, slot__starts_at__lt=min(until, utc_now()), **scope)
    attendance = {row["attendance"]: row["n"] for row in held.values("attendance").annotate(
        n=Count("id"))}
    busiest = made.values("slot__service_ref").annotate(n=Count("id")).order_by("-n")[:5]
    waited = [
        (confirmed - created).total_seconds() / 3600
        for created, confirmed in made.filter(confirmed_at__isnull=False).values_list(
            "created_at", "confirmed_at")[:5000]
        if confirmed is not None
    ]
    why = {row["cancel_reason"] or "none": row["n"] for row in made.filter(
        status=BookingStatus.CANCELLED).values("cancel_reason").annotate(n=Count("id"))}
    by_doctor: dict[str, dict[str, int]] = {}
    for row in held.values("slot__provider_ref", "attendance").annotate(n=Count("id")):
        mine = by_doctor.setdefault(
            row["slot__provider_ref"], {"attended": 0, "no_show": 0, "not_recorded": 0})
        mine[row["attendance"] or "not_recorded"] += row["n"]
    weekdays = [0] * 7                                  # Monday first, by the clinic's own clock
    coming = Booking.objects.filter(
        slot__organization_ref=organization_ref,
        status__in=[BookingStatus.REQUESTED, BookingStatus.CONFIRMED],
        slot__starts_at__gte=since, slot__starts_at__lt=until, **scope)
    for starts, zone in coming.values_list("slot__starts_at", "slot__local_zone")[:5000]:
        weekdays[starts.astimezone(get_zone(zone)).weekday()] += 1
    return {
        "by_weekday": weekdays,
        "by_doctor": [
            {"provider_ref": ref, **n, "no_show_rate": _rate(n["no_show"], n["attended"])}
            for ref, n in sorted(by_doctor.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))],
        "per_day": days, "total": sum(status.values()), "cancel_reasons": why,
        "status": {k: status.get(k, 0) for k in ("requested", "confirmed", "cancelled")},
        "attendance": {"attended": attendance.get("attended", 0),
                       "no_show": attendance.get("no_show", 0),
                       "not_recorded": attendance.get("", 0)},
        "busiest_services": [{"service_ref": r["slot__service_ref"], "count": r["n"]}
                             for r in busiest],
        "hours_to_confirm": summarize(waited),
    }


FORECAST_DAYS = 7
FORECAST_HISTORY_DAYS = 56          # eight weeks of past appointments
FORECAST_MIN_HISTORY_DAYS = 14      # under two weeks of history there is nothing fair to average
FORECAST_MIN_BOOKINGS = 10          # and a handful of bookings is too few to call anything usual


def enough_history(history_days: int, past_bookings: int) -> bool:
    return history_days >= FORECAST_MIN_HISTORY_DAYS and past_bookings >= FORECAST_MIN_BOOKINGS


def usual_by_weekday(
    past: dict[dt.date, int], today: dt.date, history_days: int
) -> dict[int, float]:
    """The average number of appointments on each weekday (Monday = 0) over the last `history_days`
    days before `today`. Days with no appointment count as zero. A simple average, not a trained
    model: it says what a normal Tuesday looked like, nothing more."""
    totals = [0] * 7
    seen = [0] * 7
    for back in range(1, history_days + 1):
        day = today - dt.timedelta(days=back)
        totals[day.weekday()] += past.get(day, 0)
        seen[day.weekday()] += 1
    return {w: round(totals[w] / seen[w], 1) if seen[w] else 0.0 for w in range(7)}


def forecast_week(organization_ref: str, clock: Clock | None = None) -> dict[str, Any]:
    """For each of the next seven days: appointments already booked and what that weekday usually
    has (the clinic's own clock). Counts only. Says so plainly when there is too little history."""
    scope = _scope()
    now = utc_now(clock)
    start = now - dt.timedelta(days=FORECAST_HISTORY_DAYS + 1)
    end = now + dt.timedelta(days=FORECAST_DAYS + 2)
    rows = Booking.objects.filter(
        slot__organization_ref=organization_ref,
        status__in=[BookingStatus.REQUESTED, BookingStatus.CONFIRMED],
        slot__starts_at__gte=start, slot__starts_at__lt=end, **scope,
    ).values_list("slot__starts_at", "slot__local_zone")[:20000]
    per_day: dict[dt.date, int] = {}
    for starts, zone in rows:
        local = starts.astimezone(get_zone(zone)).date()
        per_day[local] = per_day.get(local, 0) + 1
    taken = Booking.objects.filter(slot=OuterRef("pk"), status__in=ACTIVE_BOOKING)
    open_now = (
        Slot.objects.filter(
            organization_ref=organization_ref, status=SlotStatus.OPEN, starts_at__gt=now,
            starts_at__lt=end, **scope)
        .annotate(is_taken=Exists(taken))
        .filter(is_taken=False)
        .values_list("starts_at", "local_zone")[:20000]
    )
    free: dict[dt.date, int] = {}
    for starts, zone in open_now:
        local = starts.astimezone(get_zone(zone)).date()
        free[local] = free.get(local, 0) + 1
    today = now.date()
    past_days = [d for d in per_day if d < today]
    history = (today - min(past_days)).days if past_days else 0
    enough = enough_history(history, sum(per_day[d] for d in past_days))
    usual = usual_by_weekday(per_day, today, min(history, FORECAST_HISTORY_DAYS)) if enough else {}
    days = []
    for ahead in range(FORECAST_DAYS):
        day = today + dt.timedelta(days=ahead)
        days.append({
            "date": day.isoformat(), "weekday": day.weekday(), "booked": per_day.get(day, 0),
            "open": free.get(day, 0),
            "usual": usual.get(day.weekday()) if enough else None,
        })
    return {"enough_history": enough, "history_days": history, "days": days}
