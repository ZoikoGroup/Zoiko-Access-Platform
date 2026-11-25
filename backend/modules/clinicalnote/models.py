"""Consultation notes written by a treating clinician (DEV-304, LEAD DECISION).

The lead decided (17 Nov 2026) that Zoiko may hold the text of a clinical note, encrypted, in the
clinic's own space. The text is stored only as ciphertext (foundation/crypto.py), bound to this
note so it cannot be moved to another row. A signed note is never edited: a correction is a new
version that supersedes it, with a fixed reason. Rows are never deleted; once the NHS retention
period has run out the text is erased and the row kept (DEV-324).
"""

from __future__ import annotations

from django.db import models

from foundation.models import CellScopedModel


class NoteState(models.TextChoices):
    DRAFT = "draft", "Draft"
    SIGNED = "signed", "Signed"
    SUPERSEDED = "superseded", "Replaced by a later version"


class AmendReason(models.TextChoices):
    """A fixed list, so no free text can hide in the reason."""

    ENTERED_IN_ERROR = "entered_in_error", "Entered in error"
    CLARIFICATION = "clarification", "Clarification"
    NEW_INFORMATION = "new_information", "New information"


class ClinicalNote(CellScopedModel):
    person_ref = models.CharField(max_length=22)
    organization_ref = models.CharField(max_length=22)  # the clinic that holds the note
    author_ref = models.CharField(max_length=22)  # the clinician who wrote it
    note_date = models.DateField()  # the day the consultation was
    body_enc = models.TextField()  # ciphertext only
    state = models.CharField(max_length=12, choices=NoteState.choices, default=NoteState.DRAFT)
    version = models.PositiveSmallIntegerField(default=1)
    supersedes_ref = models.CharField(max_length=22, blank=True, default="")
    superseded_by_ref = models.CharField(max_length=22, blank=True, default="")
    amend_reason = models.CharField(
        max_length=20, choices=AmendReason.choices, blank=True, default=""
    )
    signed_by_ref = models.CharField(max_length=22, blank=True, default="")
    signed_at = models.DateTimeField(null=True, blank=True)
    # The author may keep one note from a guardian (DEV-324), for example when a young person
    # spoke in confidence. The patient themselves still sees it.
    guardian_hidden = models.BooleanField(default=False)
    # When the retention period ran out and the text was erased (DEV-324, NHS rules). The row
    # stays, so the history of what existed is not lost; only the words are gone.
    erased_at = models.DateTimeField(null=True, blank=True)
    # The visit's coded diagnoses (DEV-344, LEAD DECISION): SNOMED CT code, words and status, as
    # JSON, ENCRYPTED like the text. Changed only while a draft; signed with the note.
    diagnoses_enc = models.TextField(blank=True, default="")
    # The visit's structured medications (DEV-375): SNOMED CT / dm+d concept, name, dose and status,
    # as JSON, ENCRYPTED like the text. Changed only while a draft; signed with the note.
    medications_enc = models.TextField(blank=True, default="")

    class Meta(CellScopedModel.Meta):
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="clinicalnote_scope_idx"),
            models.Index(fields=["tenant_id", "person_ref"], name="clinicalnote_person_idx"),
        ]


class NoteAddendum(CellScopedModel):
    """A short, dated text added to a SIGNED note without replacing it (DEV-309). It is never
    edited or deleted; the signed note stays exactly as it was. The text is stored encrypted."""

    note = models.ForeignKey(ClinicalNote, on_delete=models.PROTECT, related_name="+")
    author_ref = models.CharField(max_length=22)
    body_enc = models.TextField()

    class Meta(CellScopedModel.Meta):
        indexes = [models.Index(fields=["cell_id", "tenant_id"], name="noteaddendum_scope_idx")]
