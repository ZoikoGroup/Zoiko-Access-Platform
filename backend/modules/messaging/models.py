"""Secure messages between a patient and their clinic (docs: D16, GHE-16).

A THREAD belongs to one patient record and one organization (the clinic). Its subject and every
message body are stored ONLY as ciphertext (foundation/crypto.py), each bound to its own row.
Patients and organizations are referenced by public id; the other modules own them.
"""
from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class ThreadStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class Side(models.TextChoices):
    PATIENT = "patient", "Patient"
    STAFF = "staff", "Clinic"


class Thread(CellScopedModel):
    person_ref = models.CharField(max_length=22)            # the patient this is about
    organization_ref = models.CharField(max_length=22)      # the clinic that receives it
    started_by_ref = models.CharField(max_length=22)
    subject_enc = models.TextField()
    status = models.CharField(
        max_length=16, choices=ThreadStatus.choices, default=ThreadStatus.OPEN)
    # How many messages each side has not opened yet (a counter, so lists stay cheap).
    patient_unread = models.PositiveIntegerField(default=0)
    staff_unread = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField()
    first_reply_at = models.DateTimeField(null=True, blank=True)   # the clinic's first answer

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="thread_scope_idx"),
            models.Index(fields=["person_ref", "last_message_at"], name="thread_person_idx"),
            models.Index(fields=["organization_ref", "last_message_at"], name="thread_org_idx"),
        ]


class Message(CellScopedModel):
    thread = models.ForeignKey(Thread, on_delete=models.PROTECT, related_name="messages")
    author_ref = models.CharField(max_length=22)            # the login that wrote it
    side = models.CharField(max_length=16, choices=Side.choices)
    body_enc = models.TextField()

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="message_scope_idx"),
            models.Index(fields=["thread", "created_at"], name="message_thread_idx"),
        ]


class MessageView(CellScopedModel):
    """A signed-in person opened a message written by the OTHER side (GHE-16: secure viewing is its
    own state, separate from delivery and from clinical review). One row per login and message,
    written once and never changed. Opening is not reading with understanding and is never a
    clinician's acknowledgment (DEV-113)."""

    message = models.ForeignKey(Message, on_delete=models.PROTECT, related_name="views")
    viewer_ref = models.CharField(max_length=22)            # the login that opened it
    side = models.CharField(max_length=16, choices=Side.choices)   # which side that login is on

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="msgview_scope_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "message", "viewer_ref"], name="message_view_once"),
        ]
