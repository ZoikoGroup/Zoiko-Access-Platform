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


class ListingState(models.TextChoices):
    """Where a service is on its way to being offered to patients (GHE-14 WF-04). Only PUBLISHED can
    be booked or referred to. A provider may ask for a listing but cannot approve its own."""

    DRAFT = "draft", "Draft"
    EVIDENCE_PENDING = "evidence_pending", "Waiting for review"
    REVIEWED = "reviewed", "Reviewed"
    PUBLISHED = "published", "Published"
    SUSPENDED = "suspended", "Suspended"


class Location(CellScopedModel):
    """A physical or virtual site. Its time zone decides what a wall-clock slot time means."""

    organization_ref = models.CharField(max_length=22)   # trust Organization.public_id
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True, default="")
    phone = models.CharField(max_length=24, blank=True, default="")   # how patients call the site
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
    # The clinic's own on/off switch is `status`. Whether Zoiko's rules let patients see and book it
    # is `listing_state`: a service is offered only when it is active AND published.
    listing_state = models.CharField(
        max_length=20, choices=ListingState.choices, default=ListingState.DRAFT)
    # A reference to the proof (for example a registration number). Never patient details.
    evidence_note = models.CharField(max_length=200, blank=True, default="")
    requested_by_ref = models.CharField(max_length=22, blank=True, default="")
    reviewed_by_ref = models.CharField(max_length=22, blank=True, default="")
    published_by_ref = models.CharField(max_length=22, blank=True, default="")

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
