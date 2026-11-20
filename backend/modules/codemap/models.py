"""Mapping partners' local codes to SNOMED CT, decided by two people (DEV-322, LEAD DECISION).

FHIR design step 4 (docs/FHIR-DESIGN.md, GHE-13 T-29). A partner may send its own local code next to
the SNOMED CT code UK Core requires. Each local code it sends is listed here, once, in Zoiko's
platform space, as "needs review". Zoiko regional support PROPOSES the SNOMED CT concept it means; a
DIFFERENT person, the clinical safety officer, APPROVES or rejects it. Until then the code is never
guessed and the original message is kept exactly as it arrived. Rows are never deleted.
"""

from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class MappingState(models.TextChoices):
    UNMAPPED = "unmapped", "Needs review"
    PROPOSED = "proposed", "Match proposed, waiting for approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class RejectReason(models.TextChoices):
    NO_EQUIVALENT = "no_equivalent", "No SNOMED CT equivalent"
    WRONG_MATCH = "wrong_match", "The proposed match is wrong"
    OTHER = "other", "Other"


class CodeMapping(CellScopedModel):
    source_system = models.CharField(max_length=200)
    source_code = models.CharField(max_length=64)
    source_display = models.CharField(max_length=200, blank=True, default="")
    target_code = models.CharField(max_length=18, blank=True, default="")  # a SNOMED CT concept
    state = models.CharField(
        max_length=10, choices=MappingState.choices, default=MappingState.UNMAPPED
    )
    seen_count = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    proposed_by_ref = models.CharField(max_length=22, blank=True, default="")
    decided_by_ref = models.CharField(max_length=22, blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(
        max_length=20, choices=RejectReason.choices, blank=True, default=""
    )

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "source_system", "source_code"], name="codemap_one_per_code"
            )
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="codemap_scope_idx")]
