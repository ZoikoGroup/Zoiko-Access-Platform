"""Checking that a doctor's name is real (DEV-095).

A doctor chooses their own display name and nobody has checked it. An organization administrator can
record that they checked it, say against the licence register, and write down what they looked at.
A check belongs to one name: if the doctor changes the name, the check stops counting until someone
checks the new one. Patients then see a "Verified" mark only for a name that is still checked.

TEMPORARY RULE: one administrator is enough, and an administrator can check any doctor at their own
organization except themselves. Who should really do this, and whether it takes two people, is open
question 25 (backlog B7).
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence.api import AuditOutcome, record

from .display_names import display_names
from .models import Actor, ActorType, NameCheck, NameCheckStatus
from .services import clinicians_at

BASES = {
    "licence_register": "Checked on the licence or medical council register",
    "identity_document": "Checked an identity document",
    "employer_record": "Checked the employer's records",
    "other": "Checked another way (say how in the note)",
}
MAX_NOTE = 200


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _digest(actor_ref: str, name: str) -> str:
    return hashlib.sha256(f"{actor_ref}\n{name}".encode()).hexdigest()


def _text(value: object, field: str, low: int, high: int) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not low <= len(text) <= high or any(ord(ch) < 32 for ch in text):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return text


def _doctor_at(actor_ref: str, organization_ref: str) -> Actor:
    """The doctor, who must hold the doctor role at this administrator's organization."""
    actor = Actor.objects.filter(public_id=actor_ref, **_scope()).first()
    if (
        actor is None
        or actor.actor_type != ActorType.CLINICIAN
        or actor_ref not in clinicians_at(organization_ref)
    ):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return actor


def verify_name(
    actor_ref: str, *, by_ref: str, organization_ref: str, basis: str, note: str = ""
) -> dict[str, Any]:
    """Record that this doctor's current display name was checked."""
    if basis not in BASES:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["basis"])
    note = _text(note, "note", 0, MAX_NOTE) if (note or "").strip() else ""
    if basis == "other" and len(note) < 5:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["note"])
    if not by_ref or by_ref == actor_ref:
        raise ZoikoError(ErrorCode.POLICY_DENIED)  # nobody checks their own name
    with transaction.atomic():
        actor = _doctor_at(actor_ref, organization_ref)
        name = display_names().get(actor_ref)
        if not name:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["name"])  # nothing to check yet
        NameCheck.objects.update_or_create(
            actor=actor,
            cell_id=_scope()["cell_id"],
            tenant_id=_scope()["tenant_id"],
            defaults={
                "name_digest": _digest(actor_ref, name),
                "basis": basis,
                "note": note,
                "status": NameCheckStatus.VERIFIED,
                "checked_by_ref": by_ref,
                "withdrawn_by_ref": "",
                "withdrawn_reason": "",
            },
        )
        record(
            actor_ref=by_ref,
            action="actor.name_verified",
            object_type="actor",
            object_ref=actor_ref,
            outcome=AuditOutcome.ALLOWED,
        )
    return states().get(actor_ref, {"state": "none"})


def withdraw_check(
    actor_ref: str, *, by_ref: str, organization_ref: str, reason: str
) -> dict[str, Any]:
    """Take a check back, with a reason. Nothing is shown as verified afterwards."""
    reason = _text(reason, "reason", 3, MAX_NOTE)
    with transaction.atomic():
        actor = _doctor_at(actor_ref, organization_ref)
        row = NameCheck.objects.select_for_update().filter(actor=actor, **_scope()).first()
        if row is None or row.status != NameCheckStatus.VERIFIED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.status = NameCheckStatus.WITHDRAWN
        row.withdrawn_by_ref, row.withdrawn_reason = by_ref, reason
        row.save(update_fields=["status", "withdrawn_by_ref", "withdrawn_reason", "updated_at"])
        record(
            actor_ref=by_ref,
            action="actor.name_check_withdrawn",
            object_type="actor",
            object_ref=actor_ref,
            outcome=AuditOutcome.ALLOWED,
        )
    return states().get(actor_ref, {"state": "none"})


def states() -> dict[str, dict[str, Any]]:
    """actor ref -> the state of the check: verified, name_changed (the name is not the one that
    was checked) or withdrawn. A doctor with no row is simply absent (state "none")."""
    names = display_names()
    out: dict[str, dict[str, Any]] = {}
    rows = NameCheck.objects.filter(**_scope()).select_related("actor")
    for row in rows:
        ref = row.actor.public_id
        if row.status == NameCheckStatus.WITHDRAWN:
            state = "withdrawn"
        elif names.get(ref) and _digest(ref, names[ref]) == row.name_digest:
            state = "verified"
        else:
            state = "name_changed"
        out[ref] = {
            "state": state,
            "basis": row.basis,
            "basis_text": BASES.get(row.basis, ""),
            "note": row.note,
            "checked_on": row.checked_at.date().isoformat(),
            "reason": row.withdrawn_reason,
        }
    return out


def verified_providers() -> set[str]:
    """The doctors whose CURRENT name has been checked."""
    return {ref for ref, s in states().items() if s["state"] == "verified"}
