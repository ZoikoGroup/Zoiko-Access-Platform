"""Audit events: an append-only, tamper-evident record of who did what.

Each row stores the hash of the previous row for the same tenant, so editing or removing an
old row breaks every hash after it. The database also refuses UPDATE and DELETE (see the
migration), so the chain is protected twice.
"""
from __future__ import annotations

from typing import Any

from django.db import models

from foundation.models import CellScopedModel

GENESIS_HASH = "0" * 64  # "previous hash" of the first event in a tenant's chain


class AuditOutcome(models.TextChoices):
    ALLOWED = "allowed", "Allowed"
    DENIED = "denied", "Denied"
    FAILED = "failed", "Failed"


class AuditEvent(CellScopedModel):
    chain_seq = models.PositiveBigIntegerField()  # 1, 2, 3... per tenant, no gaps
    occurred_at = models.DateTimeField()
    actor_ref = models.CharField(max_length=64)  # public_id of the actor, or "system"
    action = models.CharField(max_length=100)  # e.g. booking.confirm
    object_type = models.CharField(max_length=64)
    object_ref = models.CharField(max_length=64)  # public_id of the object touched
    outcome = models.CharField(max_length=16, choices=AuditOutcome.choices)
    reason_code = models.CharField(max_length=64, blank=True, default="")
    request_id = models.CharField(max_length=64)
    details = models.JSONField(default=dict)  # flat, reference-only (foundation.payloads)
    prev_hash = models.CharField(max_length=64)
    event_hash = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="audit_scope_idx"),
            models.Index(fields=["object_type", "object_ref"], name="audit_object_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "chain_seq"], name="audit_chain_unique"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("audit events are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise PermissionError("audit events are append-only")
