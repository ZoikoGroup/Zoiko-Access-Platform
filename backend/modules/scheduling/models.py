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


class Attendance(models.TextChoices):
    """What happened at a confirmed appointment, recorded by the clinic after it started."""

    ATTENDED = "attended", "Attended"
    NO_SHOW = "no_show", "Did not attend"


class Booking(CellScopedModel):
    slot = models.ForeignKey(Slot, on_delete=models.PROTECT, related_name="bookings")
    person_ref = models.CharField(max_length=22)       # trust Person.public_id
    booked_by_ref = models.CharField(max_length=22)    # trust Actor.public_id
    status = models.CharField(
        max_length=16, choices=BookingStatus.choices, default=BookingStatus.REQUESTED
    )
    cancelled_by_ref = models.CharField(max_length=22, blank=True, default="")
    # Why it was cancelled: a code from CANCEL_REASONS (empty when nobody said).
    cancel_reason = models.CharField(max_length=24, blank=True, default="")
    # A change the patient asked for on a CONFIRMED booking that the clinic has not yet answered
    # (GHE-14 WF-13, T-26, T-27): "cancel" = wants it cancelled, "rebook" = asked to move it to a
    # new time. The booking stays confirmed, and keeps its time, until the clinic acts.
    pending_change = models.CharField(max_length=10, blank=True, default="")
    # The booking this one replaced, when the patient or the clinic moved an appointment.
    rescheduled_from_ref = models.CharField(max_length=22, blank=True, default="")
    reminder_sent_at = models.DateTimeField(null=True, blank=True)   # the patient was reminded
    # When the clinic confirmed a REQUESTED booking (empty for older bookings and for a move
    # that staff made already confirmed): used for response-time reports.
    confirmed_at = models.DateTimeField(null=True, blank=True)
    attendance = models.CharField(max_length=16, choices=Attendance.choices, blank=True, default="")
    attendance_by_ref = models.CharField(max_length=22, blank=True, default="")
    attendance_at = models.DateTimeField(null=True, blank=True)

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


# Why a booking was cancelled. A patient or guardian picks from the first list, clinic staff from
# the second: the code says who it came from, and a reason from the wrong list is refused.
PATIENT_CANCEL_REASONS = {
    "schedule_conflict": "I cannot make that time",
    "no_longer_needed": "I no longer need it",
    "booked_elsewhere": "I booked somewhere else",
    "other": "Another reason",
}
CLINIC_CANCEL_REASONS = {
    "clinic_unavailable": "The clinic is closed or unavailable then",
    "doctor_unavailable": "The doctor is unavailable then",
    "service_changed": "The service has changed",
    "other": "Another reason",
}


class BookingEventSource(models.TextChoices):
    PATIENT = "patient", "The patient or their family"
    CLINIC = "clinic", "The clinic"
    SYSTEM = "system", "Automatic"


class BookingEvent(CellScopedModel):
    """One step in the life of a booking, written in the same transaction as the step itself.

    Append-only: the database refuses UPDATE and DELETE (see the migration). Each row carries the
    digest of the one before it for the same booking, so a changed or missing row is detectable
    (docs/schema README, standard column set B). It holds no patient detail, only references.
    """

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="events")
    seq = models.PositiveIntegerField()                 # 1, 2, 3... for this booking, no gaps
    event_type = models.CharField(max_length=24)
    state_from = models.CharField(max_length=16, blank=True, default="")
    state_to = models.CharField(max_length=16)
    source = models.CharField(max_length=8, choices=BookingEventSource.choices)
    actor_ref = models.CharField(max_length=64)         # a login's public id, or "system"
    reason_code = models.CharField(max_length=24, blank=True, default="")
    # The other booking of a move.
    related_ref = models.CharField(max_length=22, blank=True, default="")
    occurred_at = models.DateTimeField()
    # How this row's digest was made: 1 = the first layout, 2 = the shared chain helper. Rows are
    # never rewritten, so old rows keep checking against the layout they were written with.
    digest_version = models.PositiveSmallIntegerField()
    prev_digest = models.CharField(max_length=64)
    record_digest = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="bookingevent_scope_idx")]
        constraints = [
            # Two writers racing on one booking cannot both take the same place in its chain.
            models.UniqueConstraint(fields=["booking", "seq"], name="bookingevent_one_per_seq"),
        ]
