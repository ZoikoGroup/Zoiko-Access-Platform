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


class BatchStatus(models.TextChoices):
    PREPARED = "prepared", "Waiting for a second administrator"
    DONE = "done", "Anonymised"
    WITHDRAWN = "withdrawn", "Withdrawn"


class ErasureBatch(CellScopedModel):
    """One approved round of anonymising records past their period (DEV-398, decided 27 Nov 2026:
    nothing is removed until a person approves each batch). One administrator prepares it, which
    fixes the cut-off and how many records it would touch; a DIFFERENT administrator approves it,
    and only then does it run. Rows are never deleted."""

    organization_ref = models.CharField(max_length=22)
    data_class = models.CharField(max_length=40)
    based_on_rule = models.CharField(max_length=22)           # the rule in force it rests on
    cutoff = models.DateTimeField()
    expected_count = models.PositiveIntegerField()
    done_count = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=BatchStatus.choices,
                              default=BatchStatus.PREPARED)
    prepared_by_ref = models.CharField(max_length=22)
    decided_by_ref = models.CharField(max_length=22, blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "data_class"], condition=Q(status="prepared"),
                name="retention_one_open_batch"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="retention_batch_scope_idx")]
