"""Base class for every table that belongs to a cell and a tenant."""
from __future__ import annotations

from typing import Any

from django.db import models

from .cell_context import require_context
from .errors import ErrorCode, ZoikoError
from .ids import new_internal_id, new_public_id
from .timeutil import utc_now


class CellScopedModel(models.Model):
    """Standard columns (docs/schema README section 4, set A).

    id             internal key (UUIDv7). Never leaves the cell.
    public_id      random id for URLs, APIs and events.
    cell_id        stamped from the bound context on creation. Never taken from a request body.
    tenant_id      same.
    entity_version starts at 1; services use it to detect two people editing at once.
    """

    id = models.UUIDField(primary_key=True, default=new_internal_id, editable=False)
    public_id = models.CharField(max_length=22, unique=True, default=new_public_id, editable=False)
    cell_id = models.CharField(max_length=32, editable=False)
    tenant_id = models.UUIDField(editable=False)
    entity_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=utc_now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        # No shared index here: Django limits index names to 30 characters, and one naming
        # template cannot fit every future table. Each model declares its own, e.g.
        #   models.Index(fields=["cell_id", "tenant_id"], name="booking_scope_idx")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding:
            ctx = require_context()
            if self.cell_id and self.cell_id != ctx.cell_id:
                raise ZoikoError(ErrorCode.POLICY_DENIED)
            if self.tenant_id and self.tenant_id != ctx.tenant_id:
                raise ZoikoError(ErrorCode.POLICY_DENIED)
            self.cell_id = ctx.cell_id
            self.tenant_id = ctx.tenant_id
        super().save(*args, **kwargs)
