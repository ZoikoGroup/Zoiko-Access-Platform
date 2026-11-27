"""Clinical references for retention (DEV-213, DEV-220, DEV-359).

The report counts pointers to records about a day before the period. Erasing (only when switched
on, and only when a person asks) blanks the record's number in the clinic's own system, which is
the one thing that leads to the patient's record, and marks the pointer withdrawn "erased after
the retention period". What kind of record, its date and which clinic held it stay, as does the
audit trail."""
from __future__ import annotations

import datetime as dt

from django.db import transaction

from foundation.cell_context import require_context
from foundation.outbox import emit
from foundation.timeutil import utc_now

from .models import ClinicalReference, ReferenceStatus, WithdrawReason


def count_older(cutoff: dt.datetime, held: frozenset[str]) -> int:
    """Pointers of the bound customer to records about a day before the cut-off, not yet erased."""
    ctx = require_context()
    return (ClinicalReference.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                                             refers_to__lt=cutoff.date())
            .exclude(person_ref__in=held).exclude(external_id="").count())


def _due(cutoff: dt.datetime, held: frozenset[str], organization_ref: str):
    ctx = require_context()
    return (ClinicalReference.objects.filter(
        cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, organization_ref=organization_ref,
        refers_to__lt=cutoff.date())
        .exclude(person_ref__in=held).exclude(external_id=""))


def due_for_erasure(cutoff: dt.datetime, held: frozenset[str], organization_ref: str) -> int:
    return _due(cutoff, held, organization_ref).count()


def erase_older(cutoff: dt.datetime, held: frozenset[str], organization_ref: str) -> int:
    """Erase this clinic's pointers past the cut-off, leaving out every patient on hold."""
    now = utc_now()
    erased = 0
    people: set[str] = set()
    with transaction.atomic():
        for ref in _due(cutoff, held, organization_ref).select_for_update():
            people.add(ref.person_ref)
            ref.external_id = ""
            if ref.status == ReferenceStatus.ACTIVE:
                ref.status, ref.withdrawn_reason = (
                    ReferenceStatus.WITHDRAWN, WithdrawReason.RETENTION_PERIOD)
                ref.withdrawn_by_ref, ref.withdrawn_at = "system", now
            ref.entity_version += 1
            ref.save()
            erased += 1
        # Each patient is told once per erasure, in the same transaction (DEV-369). The notice
        # names the clinic, never a record.
        for person in sorted(people):
            emit(event_type="reference.erased.v1", aggregate_type="person", aggregate_ref=person,
                 payload={"person_ref": person, "organization_ref": organization_ref})
    return erased
