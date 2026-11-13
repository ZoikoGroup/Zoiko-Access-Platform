"""What an employer sponsor and a payer keep in their own customer space (TEMPORARY, DEV-159).

The specification names the employer and payer portals (GHE-15, GHE-17) but not their data. These
are the smallest records that make each portal do something real, and they hold no health data:

* an employer enrols people by an opaque reference of its own choosing (never a name) and only ever
  gets COUNTS back, with small groups hidden, so no person can be picked out;
* a payer keeps plans (which kinds of service they cover and the patient's share) and its members
  (the payer's own member number), and can check whether a member is covered.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class MemberState(models.TextChoices):
    ACTIVE = "active", "Enrolled"
    WITHDRAWN = "withdrawn", "Withdrawn"


class ProgramMember(CellScopedModel):
    """One person an employer has enrolled, known to the employer only by its own reference."""

    organization_ref = models.CharField(max_length=22)
    member_ref = models.CharField(max_length=40)
    state = models.CharField(max_length=10, choices=MemberState.choices, default=MemberState.ACTIVE)
    added_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        constraints = [models.UniqueConstraint(
            fields=["tenant_id", "organization_ref", "member_ref"], name="program_member_unique")]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="program_member_scope_idx")]


class CoveragePlan(CellScopedModel):
    """A payer's plan: which kinds of service (catalogue categories) it covers, and the share the
    patient pays."""

    organization_ref = models.CharField(max_length=22)
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    covered_categories = models.JSONField(default=list)
    copay_percent = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        constraints = [models.UniqueConstraint(
            fields=["tenant_id", "organization_ref", "code"], name="coverage_plan_code_unique")]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="coverage_plan_scope_idx")]


class PlanMember(CellScopedModel):
    """A person the payer covers, by the payer's own member number."""

    organization_ref = models.CharField(max_length=22)
    plan = models.ForeignKey(CoveragePlan, on_delete=models.PROTECT, related_name="+")
    member_id = models.CharField(max_length=40)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=MemberState.choices, default=MemberState.ACTIVE)
    added_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        constraints = [models.UniqueConstraint(
            fields=["tenant_id", "organization_ref", "member_id"], name="plan_member_unique")]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="plan_member_scope_idx")]
