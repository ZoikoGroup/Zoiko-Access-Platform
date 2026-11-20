"""Recording that a patient has died (DEV-333, LEAD DECISION).

A clinic administrator records the date of death and how the clinic knows. It is audited, never
silently changed, and only a SECOND person may withdraw it (recorded in error), so one mistake
cannot end a living patient's care. The date starts the NHS notes clock: 8 years after death.
The caller cancels the patient's future bookings in the same request.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import Person

WRITE_PERSON = "write:person"
SOURCES = {
    "death_certificate": "A death certificate or medical certificate of cause of death",
    "hospital_notice": "A notice from a hospital or another clinic",
    "family_notice": "The family told the clinic",
    "other": "Another source",
}
WITHDRAW_REASONS = {"recorded_in_error": "Recorded in error", "wrong_patient": "Wrong patient"}
EARLIEST = dt.date(1900, 1, 1)


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _person(person_ref: str) -> Person:
    found = Person.objects.select_for_update().filter(public_id=person_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def death_dict(p: Person) -> dict[str, Any]:
    return {
        "person": p.public_id,
        "died_on": p.died_on.isoformat() if p.died_on else None,
        "source": p.death_source or None,
        "recorded_by": p.death_recorded_by_ref or None,
        "recorded_at": p.death_recorded_at.isoformat() if p.death_recorded_at else None,
    }


def _audit(by: str, action: str, p: Person, reason: str = "") -> None:
    record(actor_ref=by, action=f"person.{action}", object_type="person",
           object_ref=p.public_id, outcome=AuditOutcome.ALLOWED, reason_code=reason)


def record_death(
    *, person_ref: str, died_on: Any, source: Any, by: str, decision: policy.Decision,
    organization_ref: str, clock: Clock | None = None,
) -> dict[str, Any]:
    """Record the date of death. The same record twice changes nothing; a different one while a
    date is held is refused (withdraw it first, by a second person)."""
    if not isinstance(died_on, str) or len(died_on) != 10:
        raise _bad("died_on")
    try:
        day = dt.date.fromisoformat(died_on)
    except ValueError:
        raise _bad("died_on") from None
    if not EARLIEST <= day <= utc_now(clock).date():
        raise _bad("died_on")  # never in the future
    if source not in SOURCES:
        raise _bad("source")
    with transaction.atomic():
        p = _person(person_ref)
        policy.check_receipt(decision, actor_public_id=by, operation=WRITE_PERSON,
                             organization_public_id=organization_ref, subject_ref=p.public_id)
        if p.died_on is not None:
            if (p.died_on, p.death_source) == (day, source):
                return death_dict(p)
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        p.died_on, p.death_source = day, source
        p.death_recorded_by_ref, p.death_recorded_at = by, utc_now(clock)
        p.entity_version += 1
        p.save(update_fields=["died_on", "death_source", "death_recorded_by_ref",
                              "death_recorded_at", "entity_version", "updated_at"])
        _audit(by, "death_recorded", p, source)
        return death_dict(p)


def withdraw_death(
    *, person_ref: str, reason: Any, by: str, decision: policy.Decision, organization_ref: str,
) -> dict[str, Any]:
    """A SECOND person withdraws a death recorded in error. The audit keeps both steps."""
    if reason not in WITHDRAW_REASONS:
        raise _bad("reason")
    with transaction.atomic():
        p = _person(person_ref)
        policy.check_receipt(decision, actor_public_id=by, operation=WRITE_PERSON,
                             organization_public_id=organization_ref, subject_ref=p.public_id)
        if p.died_on is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if p.death_recorded_by_ref == by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)  # never the person who recorded it
        p.died_on, p.death_source = None, ""
        p.death_recorded_by_ref, p.death_recorded_at = "", None
        p.entity_version += 1
        p.save(update_fields=["died_on", "death_source", "death_recorded_by_ref",
                              "death_recorded_at", "entity_version", "updated_at"])
        _audit(by, "death_withdrawn", p, reason)
        return death_dict(p)


def death_of(person_ref: str) -> dict[str, Any]:
    found = Person.objects.filter(public_id=person_ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return death_dict(found)


def death_dates(person_refs: list[str]) -> dict[str, str]:
    """{person: date of death} for those of these patients recorded as having died."""
    rows = Person.objects.filter(public_id__in=person_refs, died_on__isnull=False, **_scope())
    return {p.public_id: p.died_on.isoformat() for p in rows if p.died_on}

