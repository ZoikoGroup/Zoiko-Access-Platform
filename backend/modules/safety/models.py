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


class Classification(models.TextChoices):
    """What a capability is FOR. There is no default: an unclassified capability cannot go live."""

    ADMINISTRATIVE = "administrative", "Administrative: booking, messages, paperwork"
    CLINICAL_SUPPORT = "clinical_support", "Clinical support: informs a person, decides nothing"
    REGULATED = "regulated", "Regulated: may be a medical device, needs a legal manufacturer"


class PurposeStatus(models.TextChoices):
    DRAFT = "draft", "Proposed"
    APPROVED = "approved", "Approved by a safety officer"
    SUPERSEDED = "superseded", "Replaced by a newer version"
    RETIRED = "retired", "Withdrawn"


class IntendedPurpose(CellScopedModel):
    """A written statement of what one capability is for (GHE-08 AT01, AT05, AT07; DEV-184).

    Versioned and never edited: a change is a new version, and approving it supersedes the old one.
    Approval needs a safety officer who did not write it. A regulated capability also needs a legal
    manufacturer named before it can be approved.
    """

    capability = models.CharField(max_length=40)
    version = models.PositiveIntegerField(default=1)
    statement = models.CharField(max_length=500)
    classification = models.CharField(max_length=16, choices=Classification.choices)
    intended_users = models.CharField(max_length=200, blank=True, default="")
    legal_manufacturer = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(
        max_length=12, choices=PurposeStatus.choices, default=PurposeStatus.DRAFT)
    author_ref = models.CharField(max_length=22)
    approved_by_ref = models.CharField(max_length=22, blank=True, default="")
    # AT02: a version that changes the class of an approved capability needs two reviewers.
    reclassifies = models.BooleanField(default=False)
    first_reviewer_ref = models.CharField(max_length=22, blank=True, default="")
    note = models.CharField(max_length=300, blank=True, default="")      # why it was withdrawn

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "capability", "version"],
                                    name="purpose_version_uq"),
            # one approved statement per capability at any time
            models.UniqueConstraint(fields=["tenant_id", "capability"],
                                    condition=models.Q(status="approved"),
                                    name="purpose_one_approved"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="purpose_scope_idx")]
