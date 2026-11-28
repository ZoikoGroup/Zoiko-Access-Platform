"""The go-live readiness register (docs GHE-20): what has to be signed before a release may go
live in a country, and a record of who signed or vetoed.

It RECORDS and EVALUATES. It never deploys, never switches anything on and has no "active" state:
the specification's pilot and live stages need cohort control, rollback and a local operating
rota that do not exist (DEV-137). A decision here is never evidence of a licence.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class Decision(models.TextChoices):
    NO_GO = "no_go", "Not ready"
    G3_READY = "g3_ready", "Ready for production (G3)"
    G4_AUTHORIZED = "g4_authorized", "Authorised for the country (G4)"


class Release(CellScopedModel):
    """A release tuple: exactly what would go live, where and with which capabilities. It never
    changes; a different tuple is a different release, so no approval can be carried across."""

    release_ref = models.CharField(max_length=64)
    build = models.CharField(max_length=80)                   # a build label or digest
    cell = models.CharField(max_length=32)
    country = models.CharField(max_length=2)
    capabilities = models.JSONField(default=list)
    tuple_digest = models.CharField(max_length=64)
    decision = models.CharField(max_length=14, choices=Decision.choices, default=Decision.NO_GO)
    blockers = models.JSONField(default=list)
    created_by_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "release_ref"],
                                    name="readiness_release_unique"),
        ]
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="readiness_rel_scope_idx")]


class Evidence(CellScopedModel):
    """One signed attestation or one veto. Append-only (the database refuses UPDATE and DELETE). It
    names the tuple digest it was given for, so it counts for that exact release only."""

    release = models.ForeignKey(Release, on_delete=models.PROTECT, related_name="+")
    requirement = models.CharField(max_length=32)
    outcome = models.CharField(max_length=8)                  # signed | vetoed
    signer_ref = models.CharField(max_length=22)
    signer_role = models.CharField(max_length=8)
    reference = models.CharField(max_length=64)               # id of the signed artefact elsewhere
    tuple_digest = models.CharField(max_length=64)
    expires_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="readiness_ev_scope_idx"),
            models.Index(fields=["release", "requirement"], name="readiness_ev_req_idx"),
        ]


class GateDecision(CellScopedModel):
    """Every time the decision for a release CHANGES, one row. Append-only."""

    release = models.ForeignKey(Release, on_delete=models.PROTECT, related_name="+")
    decision = models.CharField(max_length=14)
    blockers = models.JSONField(default=list)
    trigger = models.CharField(max_length=32)                 # what caused the re-evaluation
    actor_ref = models.CharField(max_length=22)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="readiness_gd_scope_idx")]


class NamedTitle(models.TextChoices):
    """Who Zoiko named for what (decided 27 Nov 2026, DEV-396 and DEV-397)."""

    KEY_RECOVERY = "key_recovery", "Holds a sealed recovery copy of the master key"
    CTO = "cto", "Chief technology officer: signs production releases"
    CLINICAL_SAFETY_LEAD = "clinical_safety_lead", "Clinical safety lead: signs production releases"


class NamedRole(CellScopedModel):
    """One person named for one responsibility. A support officer names them; only the person
    named can confirm (they accept, or confirm they hold the sealed copy); withdrawing ends it.
    A name never counts until it is confirmed, and never after it is withdrawn."""

    title = models.CharField(max_length=24, choices=NamedTitle.choices)
    holder_ref = models.CharField(max_length=22)
    key_id = models.CharField(max_length=32, blank=True, default="")      # key holders only
    envelope_code = models.CharField(max_length=40, blank=True, default="")  # where it is sealed
    appointed_by_ref = models.CharField(max_length=22)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by_ref = models.CharField(max_length=22, blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="readiness_named_scope_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "title", "holder_ref", "key_id"],
                condition=models.Q(withdrawn_at__isnull=True), name="readiness_named_once"),
            models.UniqueConstraint(
                fields=["tenant_id", "title"],
                condition=models.Q(withdrawn_at__isnull=True,
                                   title__in=["cto", "clinical_safety_lead"]),
                name="readiness_one_signer_each"),
        ]
