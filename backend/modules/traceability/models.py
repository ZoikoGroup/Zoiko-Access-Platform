"""The requirements matrix (docs GHE-19): each requirement the specification states, how far it is
met, and the evidence behind that. It is a RECORD kept honest by one rule (a requirement cannot be
marked met without naming the automated test that proves it); it does not run any test and does not
decide whether the matrix is "good enough" (DEV-138).
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class Standing(models.TextChoices):
    MET = "met", "Met"
    PARTLY = "partly", "Partly"
    NOT_BUILT = "not_built", "Not built"


class Requirement(CellScopedModel):
    source = models.CharField(max_length=16)                  # the specification, e.g. "GHE-12"
    req_id = models.CharField(max_length=24)                  # the specification's own id
    group = models.CharField(max_length=80, blank=True, default="")
    title = models.CharField(max_length=500)
    status = models.CharField(max_length=10, choices=Standing.choices, default=Standing.NOT_BUILT)
    evidence = models.CharField(max_length=1000, blank=True, default="")
    updated_by_ref = models.CharField(max_length=22, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "source", "req_id"],
                                    name="traceability_req_unique"),
        ]
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="traceability_scope_idx"),
            models.Index(fields=["source", "status"], name="traceability_status_idx"),
        ]
