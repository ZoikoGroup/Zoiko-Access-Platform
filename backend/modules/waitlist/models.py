"""The waiting list: patients who want a service that has no free time right now.

Services, organizations and patients belong to other modules: referenced by public id.
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q

from foundation.models import CellScopedModel


class WaitState(models.TextChoices):
    WAITING = "waiting", "Waiting"
    OFFERED = "offered", "A time opened up"
    CLOSED = "closed", "Closed"


OPEN_STATES = (WaitState.WAITING, WaitState.OFFERED)


class CloseReason(models.TextChoices):
    BOOKED = "booked", "Booked"
    LEFT = "left", "Left the list"


class WaitEntry(CellScopedModel):
    person_ref = models.CharField(max_length=22)            # the patient
    service_ref = models.CharField(max_length=22)
    organization_ref = models.CharField(max_length=22)      # copied from the service, for policy
    joined_by_ref = models.CharField(max_length=22)         # the login that added them
    state = models.CharField(max_length=16, choices=WaitState.choices, default=WaitState.WAITING)
    offered_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(
        max_length=16, choices=CloseReason.choices, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="waitlist_scope_idx"),
            models.Index(fields=["person_ref"], name="waitlist_person_idx"),
            models.Index(fields=["service_ref", "state", "created_at"], name="waitlist_queue_idx"),
        ]
        constraints = [
            # A patient is on the list for a service at most once at a time.
            models.UniqueConstraint(
                fields=["tenant_id", "person_ref", "service_ref"],
                condition=Q(state__in=["waiting", "offered"]),
                name="waitlist_one_open_per_person_service",
            ),
        ]
