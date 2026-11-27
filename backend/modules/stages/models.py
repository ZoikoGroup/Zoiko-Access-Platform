"""The stored steps of a booking's and a referral's standard lifecycle (DEV-360).

The lifecycle itself is worked out from the record's facts (foundation/lifecycles.py). This module
writes each new stage down the moment it is seen, so the history says when a booking really became
"pending the clinic's system" or "reconciling", even after the record has moved on. Append-only:
the database refuses changes and deletions.
"""
from __future__ import annotations

from typing import Any

from django.db import models

from foundation.models import CellScopedModel


class Subject(models.TextChoices):
    BOOKING = "booking", "Booking"
    REFERRAL = "referral", "Referral"


class StageStep(CellScopedModel):
    subject_type = models.CharField(max_length=12, choices=Subject.choices)
    subject_ref = models.CharField(max_length=22)
    seq = models.PositiveIntegerField()                 # 1, 2, 3... per subject
    state = models.CharField(max_length=32)             # a key of foundation.lifecycles
    seen_at = models.DateTimeField()                    # when the product saw this stage
    cause = models.CharField(max_length=64)             # the event type, or "observed"

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="stagestep_scope_idx"),
            models.Index(fields=["subject_type", "subject_ref", "seq"],
                         name="stagestep_subject_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "subject_type", "subject_ref", "seq"],
                                    name="stagestep_one_per_seq"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("stage steps are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise PermissionError("stage steps are append-only")
