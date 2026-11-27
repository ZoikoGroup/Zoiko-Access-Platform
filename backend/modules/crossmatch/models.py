"""Matching a patient between separate customers (DEV-363). Every row lives in Zoiko's own platform
space, never in a customer's: a customer's data never reaches another customer's tables.

What is shared is a FINGERPRINT (a keyed hash under a key only the platform holds), never a name,
date or number. Nothing is merged across customers: a confirmed match only lets each side know the
other organization also has a record for this person.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class Participation(CellScopedModel):
    """A customer that has chosen to take part. No row, or `active` false: it does not."""

    customer_ref = models.CharField(max_length=22)
    customer_name = models.CharField(max_length=120)
    active = models.BooleanField(default=False)
    changed_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="crossjoin_scope_idx")]
        constraints = [models.UniqueConstraint(fields=["tenant_id", "customer_ref"],
                                               name="crossjoin_one_per_customer")]


class SharedKey(CellScopedModel):
    """One fingerprint of one patient of one customer."""

    customer_ref = models.CharField(max_length=22)
    person_ref = models.CharField(max_length=22)
    kind = models.CharField(max_length=16)               # birth_surname | phone
    match_key = models.CharField(max_length=64)           # the fingerprint; never reversible

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="crosskey_scope_idx"),
            models.Index(fields=["kind", "match_key"], name="crosskey_lookup_idx"),
            models.Index(fields=["customer_ref", "person_ref"], name="crosskey_person_idx"),
        ]


class CaseState(models.TextChoices):
    OPEN = "open", "Waiting for both organizations"
    CONFIRMED = "confirmed", "Both say it is the same person"
    NOT_SAME = "not_same", "Not the same person"


class CrossCase(CellScopedModel):
    """A possible match between two customers' patients. Side A is the earlier customer by id."""

    customer_a_ref = models.CharField(max_length=22)
    person_a_ref = models.CharField(max_length=22)
    customer_b_ref = models.CharField(max_length=22)
    person_b_ref = models.CharField(max_length=22)
    factors = models.JSONField(default=list)             # which fingerprints were equal
    state = models.CharField(max_length=12, choices=CaseState.choices, default=CaseState.OPEN)
    a_answer = models.CharField(max_length=12, blank=True, default="")    # "" | same | not_same
    b_answer = models.CharField(max_length=12, blank=True, default="")
    a_answered_by_ref = models.CharField(max_length=22, blank=True, default="")
    b_answered_by_ref = models.CharField(max_length=22, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="crosscase_scope_idx")]
        constraints = [models.UniqueConstraint(
            fields=["tenant_id", "customer_a_ref", "person_a_ref", "customer_b_ref",
                    "person_b_ref"], name="crosscase_one_per_pair")]
