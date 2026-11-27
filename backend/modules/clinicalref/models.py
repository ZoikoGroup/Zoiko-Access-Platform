"""Clinical references: a POINTER to a record, never the record (DEV-041, decision 3).

Whether Zoiko may hold clinical notes at all is undecided, and the suggested answer is no: doctors
keep their records in their own clinical system. A reference says only that a record of a given
kind, dated a given day, exists in a named system at a clinic, under that system's own id. There is
no text, no value, no diagnosis and no file, and the withdrawal reason is a fixed choice, so no
field can carry free text. Rows are never deleted: a withdrawn reference stays, marked withdrawn.
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q

from foundation.models import CellScopedModel


class RecordKind(models.TextChoices):
    CONSULTATION_NOTE = "consultation_note", "Consultation note"
    RESULT = "result", "Result"
    LETTER = "letter", "Letter"
    IMAGING = "imaging", "Imaging"
    OTHER = "other", "Other record"


class ReferenceStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    WITHDRAWN = "withdrawn", "Withdrawn"


class WithdrawReason(models.TextChoices):
    ENTERED_IN_ERROR = "entered_in_error", "Entered in error"
    WRONG_PATIENT = "wrong_patient", "Recorded for the wrong patient"
    REMOVED_AT_SOURCE = "removed_at_source", "No longer held in the clinic's system"
    OTHER = "other", "Other"
    RETENTION_PERIOD = "retention_period", "Erased after the retention period"   # system only


class ClinicalReference(CellScopedModel):
    person_ref = models.CharField(max_length=22)
    organization_ref = models.CharField(max_length=22)      # the clinic that holds the record
    author_ref = models.CharField(max_length=22)            # the clinician who added the pointer
    system = models.CharField(max_length=40)                # short code of the clinic's system
    external_id = models.CharField(max_length=64)           # that system's id; stays in the cell
    kind = models.CharField(max_length=20, choices=RecordKind.choices)
    refers_to = models.DateField()                          # the day the record is about
    status = models.CharField(max_length=12, choices=ReferenceStatus.choices,
                              default=ReferenceStatus.ACTIVE)
    withdrawn_reason = models.CharField(max_length=24, choices=WithdrawReason.choices,
                                        blank=True, default="")
    withdrawn_by_ref = models.CharField(max_length=22, blank=True, default="")
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta(CellScopedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "organization_ref", "person_ref", "system", "external_id"],
                condition=Q(status="active"), name="clinicalref_one_active_pointer"),
        ]
        indexes = [
            models.Index(fields=["cell_id", "tenant_id"], name="clinicalref_scope_idx"),
            models.Index(fields=["tenant_id", "person_ref"], name="clinicalref_person_idx"),
        ]
