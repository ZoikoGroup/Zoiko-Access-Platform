"""The hazard register (docs: `safety.hazard`, schema file 05 section A): Zoiko's safety records.

A hazard is a way the product could hurt someone. The docs seed 20 product hazards (H01 to H20) and
keep the AI hazards (HAI-01 to HAI-16) as a separate library. Each is assessed, independently
reviewed, and only then may its remaining risk be accepted. The scales below are NOT in the docs
(marked INFERRED in the register, DEV-103): severity and probability run 1 to 5, and the remaining
risk is low, medium or high.
"""

from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class Library(models.TextChoices):
    PRODUCT = "product", "Product hazards (H01 to H20)"
    AI = "ai", "AI hazards (HAI-01 to HAI-16)"


class HazardStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    VERIFIED = "VERIFIED", "Verified by an independent reviewer"
    REJECTED = "REJECTED", "Rejected by the reviewer"
    ACCEPTED = "ACCEPTED", "Remaining risk accepted"
    RETIRED = "RETIRED", "Retired"


class Hazard(CellScopedModel):
    ref_no = models.CharField(max_length=12)
    library = models.CharField(max_length=8, choices=Library.choices)
    title = models.CharField(max_length=120)
    hazardous_situation = models.CharField(max_length=500, blank=True, default="")
    exposure_path = models.CharField(max_length=300, blank=True, default="")
    affected_groups = models.CharField(max_length=200, blank=True, default="")
    severity = models.PositiveSmallIntegerField(default=0)  # 0 = not assessed yet, then 1 to 5
    probability = models.PositiveSmallIntegerField(default=0)  # 0 = not assessed yet, then 1 to 5
    basis_method = models.CharField(max_length=300, blank=True, default="")
    residual_risk = models.CharField(max_length=6, blank=True, default="")  # low, medium or high
    owner_ref = models.CharField(max_length=22, blank=True, default="")
    linked_build = models.CharField(max_length=100, blank=True, default="")
    assessed_by_ref = models.CharField(max_length=22, blank=True, default="")
    reviewer_ref = models.CharField(max_length=22, blank=True, default="")
    status = models.CharField(
        max_length=10, choices=HazardStatus.choices, default=HazardStatus.OPEN
    )

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["cell_id", "tenant_id", "ref_no"], name="hazard_ref_uq"
            ),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="hazard_scope_idx")]


class HazardEvent(CellScopedModel):
    """Every step of a hazard, with who and why. Append-only in the database."""

    hazard_ref = models.CharField(max_length=22)
    action = models.CharField(max_length=20)
    from_status = models.CharField(max_length=10)
    to_status = models.CharField(max_length=10)
    actor_ref = models.CharField(max_length=22)
    note = models.CharField(max_length=300, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id", "hazard_ref"], name="hazardevent_idx")
        ]
