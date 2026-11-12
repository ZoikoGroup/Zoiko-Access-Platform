"""The workflow engine kernel (docs GHE-12): versioned definitions, running instances, a chained
history of every transition, timers and escalations.

It COORDINATES business steps and keeps the evidence; it is never the source of clinical truth and
never decides anything clinical. A step moves forward only on typed evidence, a clock running out
never proves anything, and "closed" means a defined administrative path has enough evidence, not
that anyone recovered (DEV-132).
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q

from foundation.models import CellScopedModel


class DefinitionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published (cannot change)"
    RETIRED = "retired", "Retired (no new instances)"


class State(models.TextChoices):
    """The universal administrative states (GHE-12 section 04)."""

    REQUESTED = "requested", "Requested"
    VALIDATED = "validated", "Validated"
    ACCEPTED = "accepted", "Accepted by the receiving party"
    IN_PROGRESS = "in_progress", "In progress"
    OUTPUT_AVAILABLE = "output_available", "Output available"
    CLINICAL_REVIEWED = "clinical_reviewed", "Reviewed by a clinician"
    HANDOFF_ACKNOWLEDGED = "handoff_acknowledged", "Next party accepted"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"
    BLOCKED = "blocked", "Blocked"


class Overlay(models.TextChoices):
    """Operational health, a separate dimension that never replaces the state."""

    CLEAR = "clear", "Clear"
    AT_RISK = "at_risk", "At risk"
    EXCEPTION = "exception", "Exception"
    RECONCILING = "reconciling", "Reconciling with the source"


class WorkflowDefinition(CellScopedModel):
    key = models.CharField(max_length=64)
    version = models.PositiveIntegerField(default=1)
    journey = models.CharField(max_length=64)
    path = models.JSONField(default=list)                     # the states it goes through
    timer_policies = models.JSONField(default=list)           # [{state, kind, after_minutes}]
    status = models.CharField(max_length=10, choices=DefinitionStatus.choices,
                              default=DefinitionStatus.DRAFT)
    content_hash = models.CharField(max_length=64)
    created_by_ref = models.CharField(max_length=22)
    published_by_ref = models.CharField(max_length=22, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "key", "version"],
                                    name="orch_definition_version_unique"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="orch_def_scope_idx")]


class WorkflowInstance(CellScopedModel):
    definition = models.ForeignKey(WorkflowDefinition, on_delete=models.PROTECT, related_name="+")
    definition_key = models.CharField(max_length=64)
    definition_version = models.PositiveIntegerField()       # pinned: never silently rebound
    journey = models.CharField(max_length=64)
    organization_ref = models.CharField(max_length=22)
    subject_ref = models.CharField(max_length=40)             # an opaque reference, never a name
    state = models.CharField(max_length=24, choices=State.choices, default=State.REQUESTED)
    overlay = models.CharField(max_length=12, choices=Overlay.choices, default=Overlay.CLEAR)
    owner_ref = models.CharField(max_length=22)               # the accountable person
    blocked_from = models.CharField(max_length=24, blank=True, default="")
    blocked_owner_ref = models.CharField(max_length=22, blank=True, default="")
    closure_digest = models.CharField(max_length=64, blank=True, default="")
    closed_at = models.DateTimeField(null=True, blank=True)
    # The key a start was made with and a fingerprint of what was asked, so asking again is safe
    # and asking something different under the same key is a conflict (DEV-145).
    start_key = models.CharField(max_length=64, blank=True, default="")
    start_digest = models.CharField(max_length=64, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="orch_inst_scope_idx"),
            models.Index(fields=["organization_ref", "state"], name="orch_inst_org_state_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "definition_key", "subject_ref"],
                condition=~Q(state__in=["closed", "cancelled"]),
                name="orch_one_live_per_subject"),
            models.UniqueConstraint(
                fields=["tenant_id", "start_key"], condition=~Q(start_key=""),
                name="orch_start_key_unique"),
        ]


class Transition(CellScopedModel):
    """One step in an instance's life. Append-only (the database refuses UPDATE and DELETE) and
    chained: each row holds the digest of the one before it. References only, no people."""

    instance = models.ForeignKey(WorkflowInstance, on_delete=models.PROTECT, related_name="steps")
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=12)                    # transition | overlay | owner | timer
    from_state = models.CharField(max_length=24)
    to_state = models.CharField(max_length=24)
    overlay_after = models.CharField(max_length=12)
    actor_ref = models.CharField(max_length=64)               # a login, or "system"
    role_code = models.CharField(max_length=8, blank=True, default="")
    purpose = models.CharField(max_length=32, blank=True, default="")
    evidence = models.JSONField(default=list)                 # [{"kind", "ref"}]
    note = models.CharField(max_length=300, blank=True, default="")
    idempotency_key = models.CharField(max_length=64, blank=True, default="")
    definition_version = models.PositiveIntegerField()
    policy_digest = models.CharField(max_length=64, blank=True, default="")
    decision_ref = models.CharField(max_length=40, blank=True, default="")   # the access decision
    occurred_at = models.DateTimeField()
    prev_digest = models.CharField(max_length=64)
    record_digest = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["instance", "seq"], name="orch_step_one_per_seq"),
            models.UniqueConstraint(
                fields=["instance", "idempotency_key"], condition=~Q(idempotency_key=""),
                name="orch_step_idempotent"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="orch_step_scope_idx")]


class TimerStatus(models.TextChoices):
    PENDING = "pending", "Waiting"
    FIRED = "fired", "Fired"
    STALE = "stale", "No longer needed"


class WorkflowTimer(CellScopedModel):
    instance = models.ForeignKey(WorkflowInstance, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=32)
    watches_state = models.CharField(max_length=24)           # it only matters while still here
    due_at = models.DateTimeField()
    status = models.CharField(max_length=8, choices=TimerStatus.choices,
                              default=TimerStatus.PENDING)
    owner_ref = models.CharField(max_length=22)               # who answers when it fires
    fired_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="orch_timer_scope_idx"),
            models.Index(fields=["status", "due_at"], name="orch_timer_due_idx"),
        ]


class EscalationStatus(models.TextChoices):
    OPEN = "open", "Waiting for a person"
    REACHED = "reached", "A person took ownership"


class Escalation(CellScopedModel):
    """Created when a timer fires. It is closed ONLY by a person recording that a human was reached
    and took ownership; a message sent or a timer expiring never closes it (GHE-12 WF-08)."""

    instance = models.ForeignKey(WorkflowInstance, on_delete=models.PROTECT, related_name="+")
    timer = models.ForeignKey(WorkflowTimer, on_delete=models.PROTECT, related_name="+")
    owner_ref = models.CharField(max_length=22)
    status = models.CharField(max_length=8, choices=EscalationStatus.choices,
                              default=EscalationStatus.OPEN)
    attempts = models.PositiveSmallIntegerField(default=0)    # tries that did not reach anyone
    closed_by_ref = models.CharField(max_length=22, blank=True, default="")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="orch_esc_scope_idx"),
            models.Index(fields=["status"], name="orch_esc_status_idx"),
        ]
