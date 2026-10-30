"""Creating slots, booking, confirming and cancelling.

Access: booking, confirming and cancelling need a policy RECEIPT, an ALLOW decision for exactly
this actor, operation, organization and patient (see policy.check_receipt). Callers get one
from policy.authorize(), which records denials. The domain therefore enforces access itself.

Every change is audited and emits an outbox event in the same transaction.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, QuerySet

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, TimeError, from_local, get_zone, utc_now
from modules.catalog import api as catalog
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .models import ACTIVE_BOOKING, Booking, BookingStatus, Slot, SlotStatus

WRITE_BOOKING = "write:booking"
# Only organization staff may confirm. (Patients and guardians may book and cancel.)
CONFIRMING_ROLES = frozenset({"R04", "R13"})


def _audit(by: str, action: str, object_type: str, object_ref: str) -> None:
    record(actor_ref=by, action=action, object_type=object_type, object_ref=object_ref,
           outcome=AuditOutcome.ALLOWED)


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _event(event_type: str, booking: Booking) -> None:
    emit(
        event_type=event_type, aggregate_type="booking", aggregate_ref=booking.public_id,
        payload={
            "booking_ref": booking.public_id, "slot_ref": booking.slot.public_id,
            "service_ref": booking.slot.service_ref, "status": booking.status,
        },
    )


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
    clock: Clock | None = None,
) -> list[dict[str, Any]]:
    """Add `count` back-to-back slots starting at a wall-clock time at the location.

    `first_local` is NAIVE: the number on the clinic's clock. Times that do not exist (the
    spring-forward gap) or happen twice (fall-back) are refused rather than guessed (DEV-017).
    All slots are created or none.
    """
    service = catalog.get_service(service_ref)
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation="write:catalog",
        organization_public_id=service["organization_ref"], subject_ref=None,
    )
    if not 1 <= count <= 200:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["count"])
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
    with transaction.atomic():
        for i in range(count):
            try:
                start = from_local(
                    first_local + dt.timedelta(minutes=i * step), location["time_zone"]
                ).instant
            except TimeError:
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["first_local"]) from None
            if start <= now:
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["first_local"])
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
        return [slot_dict(s) for s in created]


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
        try:
            with transaction.atomic():
                booking = Booking.objects.create(
                    slot=slot, person_ref=person_ref, booked_by_ref=actor_public_id)
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None  # someone else got it
        _audit(actor_public_id, "booking.request", "booking", booking.public_id)
        _event("booking.requested.v1", booking)
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
        booking.entity_version += 1
        booking.save(update_fields=["status", "entity_version", "updated_at"])
        _audit(actor_public_id, "booking.confirm", "booking", booking.public_id)
        _link_doctor(booking, actor_public_id)
        _event("booking.confirmed.v1", booking)
        return booking_dict(booking)


CARE_LINK_AFTER_VISIT = dt.timedelta(days=30)


def _link_doctor(booking: Booking, confirmed_by: str) -> None:
    """A confirmed appointment gives the doctor a care relationship with the patient (the link
    the access rules require before a doctor may see them). It runs until 30 days after the
    visit. If the doctor can no longer hold one, the booking is still confirmed."""
    slot = booking.slot
    if not slot.provider_ref:
        return
    try:
        with transaction.atomic():
            trust.ensure_care_relationship(
                actor_public_id=slot.provider_ref, person_public_id=booking.person_ref,
                basis=trust.CareBasis.APPOINTMENT,
                valid_until=slot.ends_at + CARE_LINK_AFTER_VISIT, by=confirmed_by)
    except ZoikoError:
        return


def cancel(
    *, booking_ref: str, actor_public_id: str, decision: policy.Decision
) -> dict[str, Any]:
    """Cancel a booking. The slot becomes bookable again."""
    with transaction.atomic():
        booking = _own_booking(booking_ref)
        policy.check_receipt(
            decision, actor_public_id=actor_public_id, operation=WRITE_BOOKING,
            organization_public_id=booking.slot.organization_ref, subject_ref=booking.person_ref,
        )
        if booking.status == BookingStatus.CANCELLED:
            return booking_dict(booking)
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_by_ref = actor_public_id
        booking.entity_version += 1
        booking.save(update_fields=["status", "cancelled_by_ref", "entity_version", "updated_at"])
        _audit(actor_public_id, "booking.cancel", "booking", booking.public_id)
        _event("booking.cancelled.v1", booking)
        return booking_dict(booking)


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
