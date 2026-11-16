"""Tables owned by the foundation: the outbox, the inbox and idempotency keys.

Django finds a model only if it is imported here, so the abstract base is re-exported too
(tests and other modules import `foundation.models.CellScopedModel`).
"""
from __future__ import annotations

import uuid

from django.db import models
from django.db.models import BigIntegerField, Func, Q, Value

from .basemodels import CellScopedModel
from .timeutil import utc_now

__all__ = [
    "CellScopedModel", "EmailAttempt", "IdempotencyKey", "InboxMessage", "MailState", "OutboxEvent",
    "OutboxState", "QueuedEmail",
]

OUTBOX_SEQUENCE = "foundation_outbox_seq"


class OutboxState(models.TextChoices):
    PENDING = "pending", "Pending"
    PUBLISHED = "published", "Published"
    QUARANTINED = "quarantined", "Quarantined"  # gave up retrying; a person must look


class OutboxEvent(CellScopedModel):
    """An event recorded in the SAME transaction as the change that caused it.

    A relay later delivers it at least once. Payloads carry references and small flags only,
    never personal or clinical content.
    """

    # Strictly increasing number from a database sequence: the delivery order.
    seq = models.BigIntegerField(
        unique=True,
        editable=False,
        db_default=Func(
            Value(OUTBOX_SEQUENCE), function="nextval", output_field=BigIntegerField()
        ),
    )
    event_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)  # random: shared
    event_type = models.CharField(max_length=100)  # e.g. booking.partner.confirmed.v1
    schema_version = models.PositiveSmallIntegerField(default=1)
    aggregate_type = models.CharField(max_length=64)
    aggregate_ref = models.CharField(max_length=64)  # public_id of the thing that changed
    ordering_key = models.CharField(max_length=128)  # events with the same key stay in order
    dedupe_key = models.CharField(max_length=128, blank=True, default="")  # "" = none
    payload = models.JSONField(default=dict)
    correlation_id = models.CharField(max_length=64)
    causation_id = models.CharField(max_length=64, blank=True, default="")
    occurred_at = models.DateTimeField()
    state = models.CharField(
        max_length=16, choices=OutboxState.choices, default=OutboxState.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=utc_now)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=64, blank=True, default="")  # exception CLASS only

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="outbox_scope_idx"),
            models.Index(fields=["state", "next_attempt_at"], name="outbox_due_idx"),
            models.Index(fields=["ordering_key", "seq"], name="outbox_order_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "dedupe_key"],
                condition=~Q(dedupe_key=""),
                name="outbox_dedupe_per_tenant",
            )
        ]


class InboxMessage(CellScopedModel):
    """Remembers which events a consumer already handled, so duplicates change nothing."""

    consumer = models.CharField(max_length=100)
    event_id = models.UUIDField()
    processed_at = models.DateTimeField(default=utc_now)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="inbox_scope_idx")]
        constraints = [
            models.UniqueConstraint(fields=["consumer", "event_id"], name="inbox_once_per_consumer")
        ]


class IdempotencyKey(CellScopedModel):
    """Remembers the answer to a request, so repeating the request repeats the answer."""

    scope = models.CharField(max_length=200)  # who + which operation
    key_hash = models.CharField(max_length=64)  # sha256 of the client key (key not kept)
    request_digest = models.CharField(max_length=64)  # sha256 of the request (not kept)
    result = models.JSONField(default=dict)  # small: references and flags only
    completed_at = models.DateTimeField(default=utc_now)
    expires_at = models.DateTimeField()

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="idem_scope_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "scope", "key_hash"], name="idempotency_once_per_scope"
            )
        ]


class MailState(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"  # gave up; a person must look


class QueuedEmail(CellScopedModel):
    """An email waiting to be sent by the worker, never inside a web request.

    Like the outbox, this table is NOT row-level secured: the worker sends for every tenant.
    It is private to foundation/mail.py. The body may hold a one-time link, so it is wiped as soon
    as the mail is sent, and the address is replaced by a masked form.
    """

    kind = models.CharField(max_length=32)               # verify_email, reset_password, ...
    to_address = models.CharField(max_length=254)
    subject = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    state = models.CharField(max_length=16, choices=MailState.choices, default=MailState.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=utc_now)
    locked_until = models.DateTimeField(null=True, blank=True)   # a claim that expires if we crash
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=64, blank=True, default="")  # exception CLASS only
    # SHA-256 of "subject, a line break, body" as queued. It stays after the body is wiped, so there
    # is evidence of exactly what was sent without keeping a one-time link (GHE-16, DEV-113).
    content_digest = models.CharField(max_length=64, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="mail_scope_idx"),
            models.Index(fields=["state", "next_attempt_at"], name="mail_due_idx"),
        ]


class EmailAttempt(models.Model):
    """One try at sending one email, written once and never changed (GHE-16 DeliveryAttempt:
    "immutable attempt history"). The queue row keeps only the latest state; this keeps every try,
    so a message that failed twice and then went through still shows both failures.

    "accepted" means the mail server took it. That is not delivery to a person and not that it was
    read. Like the queue it belongs to, this table is not tenant-scoped (the worker sends for every
    tenant) and holds no address, no text and no error message, only the error's class name.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email_ref = models.CharField(max_length=22)            # the QueuedEmail's public id
    attempt_no = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=16)              # "accepted" or "failed"
    error = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(default=utc_now, editable=False)

    class Meta:
        indexes = [models.Index(fields=["created_at"], name="mailattempt_time_idx")]
        constraints = [
            models.UniqueConstraint(fields=["email_ref", "attempt_no"], name="mailattempt_once"),
        ]

    def __str__(self) -> str:
        return f"{self.email_ref} try {self.attempt_no}: {self.outcome}"
