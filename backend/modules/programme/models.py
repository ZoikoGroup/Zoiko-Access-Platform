"""Links between an employer's enrolled person and a patient at a clinic (DEV-312, LEAD DECISION).

The employer enrols people by an opaque reference of its own (DEV-159). To count visits for them
without ever learning who they are, the employer gives each member a ONE-TIME CODE. The patient
types it into the patient app and agrees. Only then does that patient's activity count, and the
employer sees only monthly totals, never a person, a service or a clinic, and only when at least
the minimum group of different people contributed (10 by default).

This table lives in Zoiko's platform space. The employer never sees the clinic or the patient on a
row; the clinic's patient link is for the platform's own counting. The code itself is never stored,
only a keyed fingerprint of it.
"""

from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class LinkState(models.TextChoices):
    ISSUED = "issued", "Code issued, not used yet"
    REDEEMED = "redeemed", "Linked by the patient"
    REVOKED = "revoked", "Revoked"


CODE_DAYS = 30  # a code can be used for this long


class ProgrammeLink(CellScopedModel):
    employer_tenant_ref = models.CharField(max_length=22)
    employer_name = models.CharField(max_length=120)
    member_ref = models.CharField(max_length=40)  # the employer's own reference
    code_key = models.CharField(max_length=64)  # keyed fingerprint of the code
    state = models.CharField(max_length=10, choices=LinkState.choices, default=LinkState.ISSUED)
    expires_at = models.DateTimeField()
    created_by_ref = models.CharField(max_length=22)
    clinic_tenant_ref = models.CharField(max_length=22, blank=True, default="")
    person_ref = models.CharField(max_length=22, blank=True, default="")  # platform use only
    redeemed_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["employer_tenant_ref", "member_ref"],
                condition=models.Q(state__in=["issued", "redeemed"]),
                name="programme_one_live_link",
            ),
            models.UniqueConstraint(
                fields=["code_key"],
                condition=models.Q(state="issued"),
                name="programme_code_unique_while_issued",
            ),
        ]
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="programme_scope_idx"),
            models.Index(fields=["employer_tenant_ref", "state"], name="programme_emp_idx"),
        ]
