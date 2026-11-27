"""A patient's consent, purpose by purpose (GHE-05, GHE-06).

Consent here means a choice the PATIENT (or someone the clinic has approved to act for them) makes
about who may see part of their information. The cautious default is always NO: with no row,
nothing is shared. A grant may carry an end date; once it passes it simply stops counting
(nothing has to run).
Every change is one event in a chained, append-only history, so what a patient agreed to, and when,
can always be shown.

This module holds the choice. Whoever reads the information asks `is_granted`; nobody else decides.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class ConsentState(models.TextChoices):
    GRANTED = "granted", "Shared"
    WITHDRAWN = "withdrawn", "Not shared"


class ConsentGrant(CellScopedModel):
    person_ref = models.CharField(max_length=22)               # the patient the choice is about
    purpose = models.CharField(max_length=32)                  # from services.PURPOSES
    audience = models.CharField(max_length=24, default="clinic_staff")
    state = models.CharField(max_length=10, choices=ConsentState.choices,
                             default=ConsentState.WITHDRAWN)
    granted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)   # None: until the patient withdraws
    changed_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "person_ref", "purpose", "audience"],
                name="consent_one_per_person_purpose"),
        ]
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="consent_scope_idx"),
            models.Index(fields=["person_ref"], name="consent_person_idx"),
        ]


class ConsentEvent(CellScopedModel):
    """One change to one consent, chained and append-only: what was chosen, by whom, until when."""

    consent = models.ForeignKey(ConsentGrant, on_delete=models.PROTECT, related_name="events")
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=10)                     # granted | withdrawn
    actor_ref = models.CharField(max_length=64)
    occurred_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    prev_digest = models.CharField(max_length=64)
    record_digest = models.CharField(max_length=64)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["consent", "seq"], name="consent_event_one_per_seq"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="consentevent_scope_idx")]
