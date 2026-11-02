"""A patient's insurance coverage (docs: `finance.coverage_policy`, D12).

The insurer is a plain NAME for now: insurers are not customers of the platform yet, so there is no
payer organization to point at. The member id is stored ONLY as ciphertext (foundation/crypto.py).
Patients are referenced by public id; the other modules own them.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class CoverageStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ENDED = "ended", "Ended"


class Coverage(CellScopedModel):
    person_ref = models.CharField(max_length=22)            # the insured patient
    insurer_name = models.CharField(max_length=80)
    plan_name = models.CharField(max_length=80, blank=True, default="")
    member_ref_enc = models.TextField()                 # the member or policy number, encrypted
    # The last four characters, only to recognise a card in a list.
    member_last4 = models.CharField(max_length=4, blank=True, default="")
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=CoverageStatus.choices, default=CoverageStatus.ACTIVE)
    added_by_ref = models.CharField(max_length=22)
    ended_by_ref = models.CharField(max_length=22, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="coverage_scope_idx"),
            models.Index(fields=["person_ref", "status"], name="coverage_person_idx"),
        ]
