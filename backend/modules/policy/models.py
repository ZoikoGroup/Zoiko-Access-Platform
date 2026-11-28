"""Every access decision is stored, allowed or denied, and can never be changed afterwards."""
from __future__ import annotations

from typing import Any

from django.db import models

from foundation.models import CellScopedModel


class AccessDecision(CellScopedModel):
    actor_ref = models.CharField(max_length=64)
    operation = models.CharField(max_length=64)          # e.g. read:person
    purpose_code = models.CharField(max_length=32)
    organization_ref = models.CharField(max_length=64, blank=True, default="")
    subject_ref = models.CharField(max_length=64, blank=True, default="")
    allowed = models.BooleanField()
    reason_code = models.CharField(max_length=32)
    role_code = models.CharField(max_length=16, blank=True, default="")  # role that allowed it
    policy_version = models.CharField(max_length=40)
    request_id = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="decision_scope_idx"),
            models.Index(fields=["actor_ref", "created_at"], name="decision_actor_idx"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise PermissionError("access decisions are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise PermissionError("access decisions are append-only")
