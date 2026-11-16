"""Data-rights requests (docs: `policy.rights_case`, schema file 02): a patient formally asks the
clinic about their own data. The seven request types are the specification's; the states and the
steps below are NOT (the schema marks the state list INFERRED), and no due date is set because the
specification computes it per country pack, which does not exist yet (DEV-126).
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class RequestType(models.TextChoices):
    ACCESS = "access", "See the data you hold about me"
    CORRECTION = "correction", "Correct my data"
    ERASURE = "erasure", "Delete my data"
    PORTABILITY = "portability", "Give me my data to take elsewhere"
    RESTRICTION = "restriction", "Limit how you use my data"
    OBJECTION = "objection", "I object to how you use my data"
    CONSENT_WITHDRAWAL = "consent_withdrawal", "I withdraw my consent"


class CaseState(models.TextChoices):
    RECEIVED = "received", "Received"
    IN_PROGRESS = "in_progress", "Being looked at"
    COMPLETED = "completed", "Done"
    DECLINED = "declined", "Declined"
    WITHDRAWN = "withdrawn", "Withdrawn by you"


OPEN_STATES = (CaseState.RECEIVED, CaseState.IN_PROGRESS)


class RightsCase(CellScopedModel):
    ref_no = models.CharField(max_length=14)                  # DSR-XXXXXXXX
    person_ref = models.CharField(max_length=22)              # the patient the request is about
    organization_ref = models.CharField(max_length=22)        # the clinic it was sent to
    requested_by_ref = models.CharField(max_length=22)        # the login that sent it
    request_type = models.CharField(max_length=20, choices=RequestType.choices)
    note_enc = models.TextField(blank=True, default="")       # the patient's own words, sealed
    state = models.CharField(max_length=16, choices=CaseState.choices, default=CaseState.RECEIVED)
    identity_checked_by_ref = models.CharField(max_length=22, blank=True, default="")
    identity_checked_at = models.DateTimeField(null=True, blank=True)
    decided_by_ref = models.CharField(max_length=22, blank=True, default="")
    decision_note = models.CharField(max_length=300, blank=True, default="")  # shown to patient
    decided_at = models.DateTimeField(null=True, blank=True)
    # When the clinic should answer, from the country pack in force WHEN the request was filed
    # (never recomputed). Empty when no signed pack gave a deadline (DEV-133).
    due_at = models.DateTimeField(null=True, blank=True)
    due_basis = models.CharField(max_length=80, blank=True, default="")   # which pack and revision

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="rights_scope_idx"),
            models.Index(fields=["person_ref"], name="rights_person_idx"),
            models.Index(fields=["organization_ref", "state"], name="rights_org_state_idx"),
        ]
