"""Partner connectors (GHE-13, GHE-22): operations Zoiko asks an outside system to perform.

Zoiko never decides that something happened in a partner's system. It records what it ASKED, what
the partner SAID, and how sure it is. The states keep those apart:

  pending    queued; nothing has been sent yet
  sent       a worker has the call in hand (if it crashes here the outcome is unknown)
  accepted   the partner acknowledged receipt: a TECHNICAL receipt, not an outcome
  confirmed  the partner (the source) says it is done
  rejected   the partner says no
  unknown    we cannot tell (timeout, lost answer): reconciliation asks the partner, nobody guesses
  dead       gave up; a person must look (redrive or resolve it by hand)
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q

from foundation.models import CellScopedModel
from foundation.timeutil import utc_now


class OperationState(models.TextChoices):
    PENDING = "pending", "Queued"
    SENT = "sent", "Being sent"
    ACCEPTED = "accepted", "Received by the partner"
    CONFIRMED = "confirmed", "Confirmed by the partner"
    REJECTED = "rejected", "Refused by the partner"
    UNKNOWN = "unknown", "Outcome unknown"
    DEAD = "dead", "Needs a person"


# States from which nothing more happens by itself.
FINAL_STATES = (OperationState.CONFIRMED, OperationState.REJECTED)
# States a worker still has to move along: waiting to be sent, or waiting for the partner's answer.
WORK_STATES = (OperationState.PENDING, OperationState.ACCEPTED, OperationState.UNKNOWN)


class ExternalOperation(CellScopedModel):
    connector_key = models.CharField(max_length=64)          # which adapter, e.g. "simulator"
    operation_type = models.CharField(max_length=64)         # e.g. "booking.confirm"
    subject_ref = models.CharField(max_length=64)            # what it is about (an id)
    organization_ref = models.CharField(max_length=22)       # the organization that asked
    idempotency_key = models.CharField(max_length=128)
    request = models.JSONField(default=dict)                 # flat references only (no PHI)
    request_digest = models.CharField(max_length=64)
    requested_by_ref = models.CharField(max_length=64)
    state = models.CharField(
        max_length=10, choices=OperationState.choices, default=OperationState.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    polls = models.PositiveSmallIntegerField(default=0)       # reconciliation questions asked
    max_polls = models.PositiveSmallIntegerField(default=8)
    next_attempt_at = models.DateTimeField(default=utc_now)
    sent_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    partner_ref = models.CharField(max_length=128, blank=True, default="")
    last_error = models.CharField(max_length=64, blank=True, default="")   # exception CLASS only
    last_code = models.CharField(max_length=64, blank=True, default="")    # partner reason code

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="extop_scope_idx"),
            models.Index(fields=["state", "next_attempt_at"], name="extop_due_idx"),
            models.Index(fields=["subject_ref"], name="extop_subject_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "connector_key", "idempotency_key"],
                name="extop_idempotent_per_connector"),
            models.CheckConstraint(
                condition=~Q(state__in=["confirmed", "rejected"]) | Q(finished_at__isnull=False),
                name="extop_final_has_finish_time"),
        ]


class OperationEventType(models.TextChoices):
    REQUESTED = "requested", "Requested"
    SENT = "sent", "Sent to the partner"
    ACCEPTED = "accepted", "Receipt acknowledged"
    CONFIRMED = "confirmed", "Confirmed by the partner"
    REJECTED = "rejected", "Refused by the partner"
    UNKNOWN = "unknown", "Outcome unknown"
    RETRY = "retry", "Will try again"
    DEAD = "dead", "Gave up"
    REDRIVEN = "redriven", "Put back in the queue by a person"
    RESOLVED = "resolved", "Settled by a person"


class OperationEvent(CellScopedModel):
    """One step in an operation's life. Append-only (the database refuses UPDATE and DELETE) and
    chained: each row carries the digest of the one before it. References and codes only."""

    operation = models.ForeignKey(
        ExternalOperation, on_delete=models.PROTECT, related_name="events")
    seq = models.PositiveIntegerField()
    event_type = models.CharField(max_length=10, choices=OperationEventType.choices)
    state_after = models.CharField(max_length=10)
    actor_ref = models.CharField(max_length=64)               # a login's id, or "system"
    code = models.CharField(max_length=64, blank=True, default="")   # reason or error class
    occurred_at = models.DateTimeField()
    prev_digest = models.CharField(max_length=64)
    record_digest = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="opevent_scope_idx")]
        constraints = [
            models.UniqueConstraint(fields=["operation", "seq"], name="opevent_one_per_seq"),
        ]


class CircuitState(models.TextChoices):
    CLOSED = "closed", "Working"
    OPEN = "open", "Paused after failures"
    HALTED = "halted", "Stopped until a person resets it"


class ConnectorCircuit(CellScopedModel):
    """Whether this customer's calls to one connector are paused (DEV-221, see circuit.py)."""

    connector_key = models.CharField(max_length=64)
    state = models.CharField(max_length=8, choices=CircuitState.choices,
                             default=CircuitState.CLOSED)
    failures = models.PositiveIntegerField(default=0)            # failed calls in a row
    open_until = models.DateTimeField(null=True, blank=True)
    cooldown_seconds = models.PositiveIntegerField(default=0)
    last_code = models.CharField(max_length=64, blank=True, default="")   # error class or code

    class Meta(CellScopedModel.Meta):
        constraints = [models.UniqueConstraint(fields=["tenant_id", "connector_key"],
                                               name="connector_one_circuit")]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="circuit_scope_idx")]


class CertStage(models.TextChoices):
    SANDBOX = "sandbox", "Being tried with synthetic data"
    CERTIFIED = "certified", "Certified, not live"
    LIVE = "live", "Live"
    SUSPENDED = "suspended", "Suspended"


class ConnectorCertification(CellScopedModel):
    """How far one version of a partner's interface has been approved (GHE-13 T-47, T-48;
    DEV-230). Kept in Zoiko's platform space, like country packs. A new profile version is a new
    row, starting again in the sandbox."""

    connector_key = models.CharField(max_length=64)
    profile_version = models.CharField(max_length=32)
    stage = models.CharField(max_length=10, choices=CertStage.choices, default=CertStage.SANDBOX)
    enrolled_by_ref = models.CharField(max_length=22)
    certified_by_ref = models.CharField(max_length=22, blank=True, default="")
    live_by_ref = models.CharField(max_length=22, blank=True, default="")
    note = models.CharField(max_length=300, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        constraints = [models.UniqueConstraint(
            fields=["tenant_id", "connector_key", "profile_version"],
            name="connector_one_certification_per_version")]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="cert_scope_idx")]
