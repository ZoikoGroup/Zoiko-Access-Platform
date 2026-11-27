"""Intended-purpose statements: what each capability is FOR, approved before it can go live.

GHE-08 AT01 (unknown purpose blocks enabling, no default classification), AT05 (a regulated
capability needs a legal manufacturer record) and AT07 (the intended users are stated). TEMPORARY
rules (DEV-184): a support or safety officer may propose, only a safety officer who did not write it
may approve, and the classification list is ours, not the documents'.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence.api import AuditOutcome, record

from .models import Classification, IntendedPurpose, PurposeStatus


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(*fields: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=list(fields))


def _text(value: object, field: str, low: int, high: int) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not low <= len(text) <= high or any(ord(ch) < 32 for ch in text):
        raise _bad(field)
    return text


def purpose_dict(p: IntendedPurpose) -> dict[str, Any]:
    return {
        "public_id": p.public_id, "capability": p.capability, "version": p.version,
        "statement": p.statement, "classification": p.classification,
        "intended_users": p.intended_users, "legal_manufacturer": p.legal_manufacturer,
        "status": p.status, "author_ref": p.author_ref, "approved_by_ref": p.approved_by_ref,
        "reclassifies": p.reclassifies, "reviews": 1 if p.first_reviewer_ref else 0,
        "reviews_needed": 2 if p.reclassifies else 1,
        "note": p.note, "created_at": p.created_at.isoformat(),
    }


def list_purposes() -> list[dict[str, Any]]:
    rows = IntendedPurpose.objects.filter(**_scope()).order_by("capability", "-version")
    return [purpose_dict(r) for r in rows]


def propose(
    *, capability: str, statement: object, classification: object, intended_users: object,
    legal_manufacturer: object, actor_ref: str,
) -> dict[str, Any]:
    """A new version of the statement for one capability, as a proposal. The caller has checked
    that `capability` is a real one."""
    clean = _text(statement, "statement", 20, 500)
    if classification not in Classification.values:
        raise _bad("classification")
    users = _text(intended_users, "intended_users", 3, 200)
    maker = " ".join(legal_manufacturer.split()) if isinstance(legal_manufacturer, str) else ""
    if len(maker) > 120 or any(ord(ch) < 32 for ch in maker):
        raise _bad("legal_manufacturer")
    with transaction.atomic():
        newest = IntendedPurpose.objects.filter(capability=capability, **_scope()).order_by(
            "-version").first()
        approved = IntendedPurpose.objects.filter(
            capability=capability, status=PurposeStatus.APPROVED, **_scope()).first()
        row = IntendedPurpose.objects.create(
            capability=capability, version=(newest.version + 1) if newest else 1,
            statement=clean, classification=classification, intended_users=users,
            legal_manufacturer=maker, author_ref=actor_ref,
            reclassifies=approved is not None and approved.classification != classification,
            **_scope())
        record(actor_ref=actor_ref, action="safety.purpose_proposed", object_type="purpose",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED,
               details={"capability": capability, "version": row.version})
        return purpose_dict(row)


def _own(ref: str) -> IntendedPurpose:
    found = IntendedPurpose.objects.select_for_update().filter(public_id=ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def approve(ref: str, *, actor_ref: str) -> dict[str, Any]:
    """Approve a proposal: not by its author, and a regulated one needs a named legal
    manufacturer. The previously approved version of the capability becomes superseded."""
    with transaction.atomic():
        row = _own(ref)
        if row.status != PurposeStatus.DRAFT:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if actor_ref == row.author_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)            # nobody approves their own text
        if row.classification == Classification.REGULATED and not row.legal_manufacturer:
            raise _bad("legal_manufacturer")                      # AT05
        if row.reclassifies:                                      # AT02: two different reviewers
            if not row.first_reviewer_ref:
                row.first_reviewer_ref = actor_ref
                row.entity_version += 1
                row.save(update_fields=["first_reviewer_ref", "entity_version", "updated_at"])
                record(actor_ref=actor_ref, action="safety.purpose_first_review",
                       object_type="purpose", object_ref=row.public_id,
                       outcome=AuditOutcome.ALLOWED, details={"capability": row.capability})
                return purpose_dict(row)                          # still a proposal
            if actor_ref == row.first_reviewer_ref:
                raise ZoikoError(ErrorCode.POLICY_DENIED)         # a second, different person
        for older in IntendedPurpose.objects.select_for_update().filter(
                capability=row.capability, status=PurposeStatus.APPROVED, **_scope()):
            older.status = PurposeStatus.SUPERSEDED
            older.entity_version += 1
            older.save(update_fields=["status", "entity_version", "updated_at"])
        row.status, row.approved_by_ref = PurposeStatus.APPROVED, actor_ref
        row.entity_version += 1
        row.save(update_fields=["status", "approved_by_ref", "entity_version", "updated_at"])
        record(actor_ref=actor_ref, action="safety.purpose_approved", object_type="purpose",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED,
               details={"capability": row.capability, "version": row.version})
        return purpose_dict(row)


def retire(ref: str, *, actor_ref: str, reason: object) -> dict[str, Any]:
    """Withdraw an approved or proposed statement, with a reason. A capability with no approved
    statement cannot go live."""
    note = _text(reason, "reason", 3, 300)
    with transaction.atomic():
        row = _own(ref)
        if row.status not in (PurposeStatus.DRAFT, PurposeStatus.APPROVED):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.status, row.note = PurposeStatus.RETIRED, note
        row.entity_version += 1
        row.save(update_fields=["status", "note", "entity_version", "updated_at"])
        record(actor_ref=actor_ref, action="safety.purpose_retired", object_type="purpose",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED,
               details={"capability": row.capability})
        return purpose_dict(row)


def missing_for(capabilities: list[str]) -> list[str]:
    """Which of these capabilities have no approved statement (sorted). Needs a bound tenant."""
    approved = set(IntendedPurpose.objects.filter(
        capability__in=capabilities, status=PurposeStatus.APPROVED, **_scope()
    ).values_list("capability", flat=True))
    return sorted(set(capabilities) - approved)
