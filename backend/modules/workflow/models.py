"""The staff work queue: one row for each thing a clinic still has to do.

It points at the booking, referral or conversation by public id and says nothing about the patient:
the kind of work is the only text. Items are opened and closed by the events of the other modules
(see subscribers.py), so the queue can never disagree with what really happened.
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q

from foundation.models import CellScopedModel


class Kind(models.TextChoices):
    CONFIRM_BOOKING = "confirm_booking", "Confirm an appointment"
    DECIDE_REFERRAL = "decide_referral", "Decide on a referral"
    ANSWER_MESSAGE = "answer_message", "Answer a patient message"


class Status(models.TextChoices):
    OPEN = "open", "Open"
    DONE = "done", "Done"
    CANCELLED = "cancelled", "Cancelled"


class WorkItem(CellScopedModel):
    organization_ref = models.CharField(max_length=22)      # the clinic that has to act
    kind = models.CharField(max_length=24, choices=Kind.choices)
    subject_ref = models.CharField(max_length=22)           # the booking, referral or thread
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    due_at = models.DateTimeField()
    assigned_to_ref = models.CharField(max_length=22, blank=True, default="")   # a staff login
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="workitem_scope_idx"),
            models.Index(
                fields=["organization_ref", "status", "due_at"], name="workitem_queue_idx"),
        ]
        constraints = [
            # One open item for each piece of work.
            models.UniqueConstraint(
                fields=["tenant_id", "kind", "subject_ref"], condition=Q(status="open"),
                name="workitem_one_open_per_subject"),
        ]


class ItemEventType(models.TextChoices):
    OPENED = "opened", "Opened"
    CLAIMED = "claimed", "Taken by a person"
    RELEASED = "released", "Given back"
    DONE = "done", "Done"
    CANCELLED = "cancelled", "No longer needed"


class WorkItemEvent(CellScopedModel):
    """One step in the life of a work item (the design's workflow transition). Append-only: the
    database refuses UPDATE and DELETE. Each row carries the digest of the one before it. It holds
    only references: nothing about a patient."""

    item = models.ForeignKey(WorkItem, on_delete=models.PROTECT, related_name="events")
    seq = models.PositiveIntegerField()                 # 1, 2, 3... for this item, no gaps
    event_type = models.CharField(max_length=12, choices=ItemEventType.choices)
    status_after = models.CharField(max_length=16)
    # A login's public id, "patient" (the patient side acted) or "system" (it closed itself).
    actor_ref = models.CharField(max_length=64)
    occurred_at = models.DateTimeField()
    prev_digest = models.CharField(max_length=64)
    record_digest = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="itemevent_scope_idx")]
        constraints = [
            models.UniqueConstraint(fields=["item", "seq"], name="itemevent_one_per_seq"),
        ]
