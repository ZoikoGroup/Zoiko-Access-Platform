"""Country (jurisdiction) capability packs, GHE-04.

A pack says which capabilities a market may use. It is configuration published by the global
governance plane and PULLED by the cell, so it holds no patient data and is cell-wide, not
tenant-scoped (like `trust.Tenant`). Nothing here is trusted because it is in the database: every
read checks the signature, the dates and the revocation epoch again, and fails closed.
"""
from __future__ import annotations

from django.db import models

from foundation.ids import new_internal_id, new_public_id
from foundation.timeutil import utc_now


class PackStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUPERSEDED = "superseded", "Replaced by a newer version"
    REVOKED = "revoked", "Revoked"


class CountryPack(models.Model):
    id = models.UUIDField(primary_key=True, default=new_internal_id, editable=False)
    public_id = models.CharField(max_length=22, unique=True, default=new_public_id, editable=False)
    cell_id = models.CharField(max_length=32, editable=False)
    country = models.CharField(max_length=2)                      # ISO 3166-1 alpha-2, upper case
    version = models.PositiveIntegerField()
    issued_at = models.DateTimeField()
    valid_from = models.DateTimeField()
    expires_at = models.DateTimeField()
    revocation_epoch = models.PositiveIntegerField()
    capabilities = models.JSONField()                             # {name: {"enabled": bool, ...}}
    key_id = models.CharField(max_length=32)                      # which signing key
    signature = models.CharField(max_length=200)                  # base64 Ed25519
    body_digest = models.CharField(max_length=64)                 # sha256 of the signed body
    status = models.CharField(
        max_length=12, choices=PackStatus.choices, default=PackStatus.ACTIVE)
    installed_at = models.DateTimeField(default=utc_now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cell_id", "country", "version"], name="packs_country_version_unique"),
        ]
        indexes = [models.Index(fields=["cell_id", "country", "status"], name="packs_lookup_idx")]

    def __str__(self) -> str:
        return f"{self.country} v{self.version}"


class RevocationState(models.Model):
    """The lowest revocation epoch this cell still accepts. It only ever goes up."""

    cell_id = models.CharField(max_length=32, primary_key=True)
    min_epoch = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(default=utc_now)

    def __str__(self) -> str:
        return f"{self.cell_id} epoch {self.min_epoch}"
