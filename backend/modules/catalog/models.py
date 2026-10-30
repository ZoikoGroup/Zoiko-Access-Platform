"""What can be booked: places (locations) and the services an organization offers.

Organizations belong to the trust module, so they are referenced by public id, never by a
database link across modules.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class Status(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class Location(CellScopedModel):
    """A physical or virtual site. Its time zone decides what a wall-clock slot time means."""

    organization_ref = models.CharField(max_length=22)   # trust Organization.public_id
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True, default="")
    time_zone = models.CharField(max_length=64)           # IANA name, e.g. Asia/Kolkata
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="location_scope_idx"),
            models.Index(fields=["organization_ref"], name="location_org_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "organization_ref", "name"], name="catalog_location_unique"
            ),
        ]


class Mode(models.TextChoices):
    IN_PERSON = "in_person", "In person"
    VIDEO = "video", "Video"


class Service(CellScopedModel):
    organization_ref = models.CharField(max_length=22)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=64)            # e.g. general_physician
    description = models.CharField(max_length=300, blank=True, default="")
    duration_minutes = models.PositiveSmallIntegerField()
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.IN_PERSON)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="service_scope_idx"),
            models.Index(fields=["organization_ref"], name="service_org_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "organization_ref", "name"], name="catalog_service_unique"
            ),
        ]
