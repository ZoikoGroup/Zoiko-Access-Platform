"""Notifications: short messages the system sends to a patient about their care.

Addressed to a PERSON (the patient record), not a login, so a guardian with an approved proxy can
see a child's notifications and a patient's own login sees theirs. The text is written in the
patient's tenant, by the worker, from the booking itself: events never carry it.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


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
