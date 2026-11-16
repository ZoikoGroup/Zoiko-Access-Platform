"""Country capability packs (docs GHE-04): the recorded rules for one country and service, how far
they have been reviewed, and who attested to them.

The specification's lifecycle goes DRAFT, VALIDATED, EXPERT_REVIEW, SIGNED, then STAGED_IN_CELL,
PREFLIGHT and CANARY, a local go-live gate, and ACTIVE. This build stops at SIGNED (plus suspended
and retired): nothing here can make a pack live, because cell staging, preflight and the local
go-live gate do not exist (DEV-128). A status here is never evidence of a licence.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class PackStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    VALIDATED = "validated", "Checked"
    EXPERT_REVIEW = "expert_review", "With the reviewers"
    SIGNED = "signed", "Approved on record"
    SUSPENDED = "suspended", "Suspended"
    RETIRED = "retired", "Retired"


class CountryPack(CellScopedModel):
    pack_ref = models.CharField(max_length=64)               # e.g. "gb-care-navigation"
    country = models.CharField(max_length=2)                 # ISO 3166-1 alpha-2
    service = models.CharField(max_length=40)
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=PackStatus.choices, default=PackStatus.DRAFT)
    manifest = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64)           # approvals bind this exact value
    author_ref = models.CharField(max_length=22)
    note = models.CharField(max_length=300, blank=True, default="")   # last reject/suspend reason

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "pack_ref", "revision"],
                                    name="jurisdiction_pack_revision_unique"),
        ]
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="jurisdiction_scope_idx"),
            models.Index(fields=["country", "status"], name="jurisdiction_country_idx"),
        ]


class PackApproval(CellScopedModel):
    pack = models.ForeignKey(CountryPack, on_delete=models.PROTECT, related_name="+")
    role = models.CharField(max_length=16)                   # "legal" or "clinical"
    approver_ref = models.CharField(max_length=22)
    content_hash = models.CharField(max_length=64)           # the exact content that was approved

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["pack", "role"],
                                    name="jurisdiction_one_approval_per_role"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"],
                                name="jurisdiction_appr_scope_idx")]
