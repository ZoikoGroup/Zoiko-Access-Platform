"""Bookable time slots and the bookings made against them.

Services, locations and patients belong to other modules, so they are referenced by public id.
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q

from foundation.models import CellScopedModel


class SlotStatus(models.TextChoices):
    OPEN = "open", "Open"
    WITHDRAWN = "withdrawn", "Withdrawn"  # the clinic took it off the calendar


class Slot(CellScopedModel):
    service_ref = models.CharField(max_length=22)
    location_ref = models.CharField(max_length=22)
    organization_ref = models.CharField(max_length=22)  # copied from the service, for policy
    starts_at = models.DateTimeField()                   # UTC
    ends_at = models.DateTimeField()                     # UTC
    local_zone = models.CharField(max_length=64)         # the clinic's IANA zone, kept for display
    provider_ref = models.CharField(max_length=22, blank=True, default="")  # the doctor, if any
    status = models.CharField(max_length=16, choices=SlotStatus.choices, default=SlotStatus.OPEN)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="slot_scope_idx"),
            models.Index(fields=["service_ref", "starts_at"], name="slot_service_time_idx"),
        ]
        constraints = [
            # Two different doctors may offer the same service at the same time and place...
            models.UniqueConstraint(
                fields=["tenant_id", "service_ref", "location_ref", "starts_at", "provider_ref"],
                name="scheduling_slot_unique",
            ),
            # ...but one doctor cannot start two appointments at the same moment.
            models.UniqueConstraint(
                fields=["tenant_id", "provider_ref", "starts_at"],
                condition=~models.Q(provider_ref=""),
                name="scheduling_doctor_not_double_booked",
            ),
        ]


class BookingStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"


ACTIVE_BOOKING = (BookingStatus.REQUESTED, BookingStatus.CONFIRMED)


class Booking(CellScopedModel):
    slot = models.ForeignKey(Slot, on_delete=models.PROTECT, related_name="bookings")
    person_ref = models.CharField(max_length=22)       # trust Person.public_id
    booked_by_ref = models.CharField(max_length=22)    # trust Actor.public_id
    status = models.CharField(
        max_length=16, choices=BookingStatus.choices, default=BookingStatus.REQUESTED
    )
    cancelled_by_ref = models.CharField(max_length=22, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="booking_scope_idx"),
            models.Index(fields=["person_ref"], name="booking_person_idx"),
        ]
        constraints = [
            # The database itself refuses a second active booking on the same slot.
            models.UniqueConstraint(
                fields=["slot"],
                condition=Q(status__in=["requested", "confirmed"]),
                name="scheduling_one_active_booking_per_slot",
            ),
        ]
