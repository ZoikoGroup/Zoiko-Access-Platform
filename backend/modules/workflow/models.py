"""The staff work queue: one row for each thing a clinic still has to do.

It points at the booking, referral or conversation by public id and says nothing about the patient:
the kind of work is the only text. Items are opened and closed by the events of the other modules
(see subscribers.py), so the queue can never disagree with what really happened.
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q

from foundation.ids import new_internal_id, new_public_id
from foundation.models import CellScopedModel
from foundation.timeutil import utc_now


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


# --------------------------------------------------------------------------- the workflow engine
# (GHE-12). The work queue above is a task list; these tables hold the durable journeys: versioned
# definitions, running instances pinned to one version, an append-only chain of transitions, timers
# and the exceptions a timer raises.


class DefinitionState(models.TextChoices):
    RELEASED = "released", "Released"
    RETIRED = "retired", "Retired (no new instances)"


class WorkflowDefinition(models.Model):
    """One released version of a journey. Holds no patient data, so it is cell-wide (not
    tenant-scoped, like `trust.Tenant`). The body never changes after release: the database
    refuses it."""

    id = models.UUIDField(primary_key=True, default=new_internal_id, editable=False)
    public_id = models.CharField(max_length=22, unique=True, default=new_public_id, editable=False)
    cell_id = models.CharField(max_length=32, editable=False)
    key = models.CharField(max_length=64)               # e.g. "J1.booking"
    version = models.PositiveIntegerField()
    body = models.JSONField()
    body_digest = models.CharField(max_length=64)
    state = models.CharField(
        max_length=10, choices=DefinitionState.choices, default=DefinitionState.RELEASED)
    released_at = models.DateTimeField(default=utc_now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cell_id", "key", "version"], name="wfdef_key_version_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.key} v{self.version}"


class InstanceStatus(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class WorkflowInstance(CellScopedModel):
    definition = models.ForeignKey(
        WorkflowDefinition, on_delete=models.PROTECT, related_name="instances")
    subject_ref = models.CharField(max_length=64)        # what the journey is about (an id)
    organization_ref = models.CharField(max_length=22, blank=True, default="")
    state = models.CharField(max_length=40)
    status = models.CharField(
        max_length=10, choices=InstanceStatus.choices, default=InstanceStatus.RUNNING)
    owner_ref = models.CharField(max_length=64)          # the accountable party for this journey
    zone = models.CharField(max_length=64, default="UTC")   # the IANA zone deadlines are read in
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="wfinst_scope_idx"),
            models.Index(fields=["subject_ref"], name="wfinst_subject_idx"),
        ]
        constraints = [
            # One running journey of a kind for each subject.
            models.UniqueConstraint(
                fields=["tenant_id", "definition", "subject_ref"],
                condition=Q(status="running"), name="wfinst_one_running_per_subject"),
        ]


class WorkflowTransition(CellScopedModel):
    """One step of a journey. Append-only (the database refuses UPDATE and DELETE) and chained:
    each row carries the digest of the one before it. References only, nothing about a patient."""

    instance = models.ForeignKey(
        WorkflowInstance, on_delete=models.PROTECT, related_name="transitions")
    seq = models.PositiveIntegerField()
    transition = models.CharField(max_length=40)         # "start" for the first row
    from_state = models.CharField(max_length=40, blank=True, default="")
    to_state = models.CharField(max_length=40)
    actor_ref = models.CharField(max_length=64)
    actor_kind = models.CharField(max_length=10)
    evidence = models.JSONField(default=list)            # [{"kind": ..., "ref": ...}]
    occurred_at = models.DateTimeField()
    prev_digest = models.CharField(max_length=64)
    record_digest = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="wftrans_scope_idx")]
        constraints = [
            models.UniqueConstraint(fields=["instance", "seq"], name="wftrans_one_per_seq"),
        ]


class TimerStatus(models.TextChoices):
    PENDING = "pending", "Waiting"
    FIRED = "fired", "Fired"
    CANCELLED = "cancelled", "Cancelled"


class WorkflowTimer(CellScopedModel):
    """A deadline. When it passes it raises an exception for a person to look at: a timer is a
    prompt to escalate, never proof that anything happened (and a timer that fires after the
    journey ended does nothing)."""

    instance = models.ForeignKey(
        WorkflowInstance, on_delete=models.PROTECT, related_name="timers")
    name = models.CharField(max_length=40)
    state_name = models.CharField(max_length=40)         # the state that started it
    deadline_at = models.DateTimeField()                 # UTC
    zone = models.CharField(max_length=64)               # the zone the deadline was set in
    status = models.CharField(
        max_length=10, choices=TimerStatus.choices, default=TimerStatus.PENDING)
    fired_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="wftimer_scope_idx"),
            models.Index(fields=["status", "deadline_at"], name="wftimer_due_idx"),
        ]


class ExceptionStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"


class WorkflowException(CellScopedModel):
    instance = models.ForeignKey(
        WorkflowInstance, on_delete=models.PROTECT, related_name="exceptions")
    kind = models.CharField(max_length=24)               # "timer_expired"
    detail = models.CharField(max_length=64, blank=True, default="")    # the timer's name
    status = models.CharField(
        max_length=10, choices=ExceptionStatus.choices, default=ExceptionStatus.OPEN)
    raised_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by_ref = models.CharField(max_length=64, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="wfexc_scope_idx")]
