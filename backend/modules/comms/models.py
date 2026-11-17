"""Notifications: short messages the system sends to a patient about their care.

Addressed to a PERSON (the patient record), not a login, so a guardian with an approved proxy can
see a child's notifications and a patient's own login sees theirs. The text is written in the
patient's tenant, by the worker, from the booking itself: events never carry it.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class EmailPreference(CellScopedModel):
    """Whether this LOGIN wants a short email when something changes about its own care. Off until
    the person turns it on (and an email is only ever sent to an address they confirmed)."""

    actor_ref = models.CharField(max_length=22)           # trust Actor.public_id
    enabled = models.BooleanField(default=False)
    # Quiet hours (TC30): routine reminder emails wait until the window ends. Minutes after local
    # midnight, 0 to 1439, in `quiet_zone`; both empty means no quiet hours.
    quiet_start = models.PositiveSmallIntegerField(null=True, blank=True)
    quiet_end = models.PositiveSmallIntegerField(null=True, blank=True)
    quiet_zone = models.CharField(max_length=40, blank=True, default="")
    # DEV-194: also send a short text (never with care details) to the patient's own number.
    texts_enabled = models.BooleanField(default=False)
    # DEV-198: a keyed fingerprint of the number this login proved it holds, and when.
    phone_digest = models.CharField(max_length=64, blank=True, default="")
    phone_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="emailpref_scope_idx")]
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "actor_ref"], name="comms_one_pref_each"),
        ]


class Notification(CellScopedModel):
    person_ref = models.CharField(max_length=22)          # trust Person.public_id
    kind = models.CharField(max_length=32)                # booking_requested, ...
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=300)
    booking_ref = models.CharField(max_length=22, blank=True, default="")
    source_event_id = models.UUIDField()                  # the event that caused it
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="notif_scope_idx"),
            models.Index(fields=["person_ref", "created_at"], name="notif_person_idx"),
        ]
        constraints = [
            # One notification per event, even if a bug ever ran the handler twice.
            models.UniqueConstraint(
                fields=["tenant_id", "source_event_id"], name="comms_one_per_event"
            ),
        ]


class PhoneCheck(CellScopedModel):
    """One code texted to prove a phone number (DEV-198). Only fingerprints are kept: never the
    code, never the number."""

    actor_ref = models.CharField(max_length=22)
    number_digest = models.CharField(max_length=64)
    code_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id", "actor_ref"],
                                name="phonecheck_actor_idx")]
