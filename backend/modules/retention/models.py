"""Retention rules (DEV-013, decision 5; DEV-213).

A rule says how long one customer keeps one kind of data. No rule exists until someone proposes one,
and none counts until a second administrator approves it: periods are a legal decision, so the
product ships with NONE. A period of null means "no limit" (keep). Changing a rule is a new
proposal; approving it supersedes the old one. Rows are never deleted.
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q

from foundation.models import CellScopedModel


class RuleStatus(models.TextChoices):
    PROPOSED = "proposed", "Waiting for a second administrator"
    APPROVED = "approved", "In force"
    SUPERSEDED = "superseded", "Replaced"
    WITHDRAWN = "withdrawn", "Withdrawn"


class RetentionRule(CellScopedModel):
    organization_ref = models.CharField(max_length=22)
    data_class = models.CharField(max_length=40)
    period_days = models.PositiveIntegerField(null=True, blank=True)     # null: no limit
    basis = models.CharField(max_length=300)                 # e.g. the law or policy it rests on
    status = models.CharField(max_length=12, choices=RuleStatus.choices,
                              default=RuleStatus.PROPOSED)
    proposed_by_ref = models.CharField(max_length=22)
    decided_by_ref = models.CharField(max_length=22, blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "organization_ref", "data_class"],
                condition=Q(status="approved"), name="retention_one_rule_in_force"),
            models.UniqueConstraint(
                fields=["tenant_id", "organization_ref", "data_class"],
                condition=Q(status="proposed"), name="retention_one_open_proposal"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="retention_scope_idx")]
