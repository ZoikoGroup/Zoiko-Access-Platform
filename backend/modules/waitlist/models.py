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


class WaitPriority(models.IntegerChoices):
    """How soon the clinic wants this person offered a time. Set by clinic staff, with a reason;
    people at the same level are offered times in the order they joined."""

    ROUTINE = 0, "Routine"
    PRIORITY = 1, "Priority"
    URGENT = 2, "Urgent"


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
    priority = models.PositiveSmallIntegerField(
        choices=WaitPriority.choices, default=WaitPriority.ROUTINE)
    priority_reason = models.CharField(max_length=200, blank=True, default="")
    prioritized_by_ref = models.CharField(max_length=22, blank=True, default="")

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


class WaitRule(CellScopedModel):
    """One clinic's own waiting-list rule: how many hours a person has to use an offered time
    before it goes back to the queue. No row means the service default."""

    organization_ref = models.CharField(max_length=22)
    offer_hours = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="waitrule_scope_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "organization_ref"], name="waitrule_one_per_clinic"),
        ]
