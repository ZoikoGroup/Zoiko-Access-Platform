"""Code mapping and its review queue. Every function runs with the PLATFORM tenant bound; the caller
(an API view) has checked who is asking (DEV-322)."""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction

from foundation import fhir
from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import CodeMapping, MappingState, RejectReason

MAX_LIST = 200


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _audit(actor: str, action: str, row: CodeMapping) -> None:
    record(
        actor_ref=actor,
        action=f"codemap.{action}",
        object_type="code_mapping",
        object_ref=row.public_id,
        outcome=AuditOutcome.ALLOWED,
    )


def mapping_dict(m: CodeMapping) -> dict[str, Any]:
    return {
        "id": m.public_id,
        "source_system": m.source_system,
        "source_code": m.source_code,
        "source_display": m.source_display,
        "target_code": m.target_code or None,
        "state": m.state,
        "seen_count": m.seen_count,
        "last_seen_at": m.last_seen_at.isoformat() if m.last_seen_at else None,
        "proposed_by": m.proposed_by_ref or None,
        "decided_by": m.decided_by_ref or None,
        "decided_at": m.decided_at.isoformat() if m.decided_at else None,
        "reject_reason": m.reject_reason or None,
    }


def note_seen(codings: list[dict[str, str]], clock: Clock | None = None) -> int:
    """Record every local (non SNOMED CT) code a partner sent, once each, counting repeats. Returns
    how many were new. The code is only listed: nothing is guessed and no message is changed."""
    now = utc_now(clock)
    new = 0
    for c in codings:
        system, code = c.get("system", ""), c.get("code", "")
        if system == fhir.SNOMED or not system or not code:
            continue
        system, code = system[:200], code[:64]
        with transaction.atomic():
            row = (
                CodeMapping.objects.select_for_update()
                .filter(source_system=system, source_code=code, **_scope())
                .first()
            )
            if row is None:
                try:
                    with transaction.atomic():
                        row = CodeMapping.objects.create(
                            source_system=system,
                            source_code=code,
                            source_display=(c.get("display") or "")[:200],
                            **_scope(),
                        )
                    new += 1
                except IntegrityError:  # someone else listed it a moment ago
                    row = CodeMapping.objects.select_for_update().get(
                        source_system=system, source_code=code, **_scope()
                    )
            row.seen_count += 1
            row.last_seen_at = now
            row.save(update_fields=["seen_count", "last_seen_at", "updated_at"])
    return new


def _row(mapping_ref: str) -> CodeMapping:
    row = CodeMapping.objects.select_for_update().filter(public_id=mapping_ref, **_scope()).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


def propose(*, mapping_ref: str, target_code: Any, by: str) -> dict[str, Any]:
    """Regional support proposes the SNOMED CT concept a local code means."""
    if not isinstance(target_code, str) or not fhir.sctid_ok(target_code.strip()):
        raise _bad("target_code")
    with transaction.atomic():
        row = _row(mapping_ref)
        if row.state not in (MappingState.UNMAPPED, MappingState.REJECTED):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.target_code, row.state, row.proposed_by_ref = (
            target_code.strip(),
            MappingState.PROPOSED,
            by,
        )
        row.decided_by_ref, row.decided_at, row.reject_reason = "", None, ""
        row.entity_version += 1
        row.save()
        _audit(by, "proposed", row)
        return mapping_dict(row)


def approve(*, mapping_ref: str, by: str, clock: Clock | None = None) -> dict[str, Any]:
    """A DIFFERENT person, the clinical safety officer, approves a proposed match."""
    with transaction.atomic():
        row = _row(mapping_ref)
        if row.state != MappingState.PROPOSED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if row.proposed_by_ref == by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)  # nobody approves their own proposal
        row.state, row.decided_by_ref, row.decided_at = MappingState.APPROVED, by, utc_now(clock)
        row.entity_version += 1
        row.save()
        _audit(by, "approved", row)
        return mapping_dict(row)


def reject(*, mapping_ref: str, reason: Any, by: str, clock: Clock | None = None) -> dict[str, Any]:
    """The clinical safety officer rejects a proposed match, or says a code has no equivalent."""
    if reason not in RejectReason.values:
        raise _bad("reason")
    with transaction.atomic():
        row = _row(mapping_ref)
        if row.state not in (MappingState.UNMAPPED, MappingState.PROPOSED):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if row.state == MappingState.PROPOSED and row.proposed_by_ref == by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        row.state, row.reject_reason = MappingState.REJECTED, reason
        row.decided_by_ref, row.decided_at = by, utc_now(clock)
        row.entity_version += 1
        row.save()
        _audit(by, "rejected", row)
        return mapping_dict(row)


def lookup(system: str, code: str) -> str | None:
    """The approved SNOMED CT concept for a local code, or None. Never a guess."""
    row = CodeMapping.objects.filter(
        source_system=system, source_code=code, state=MappingState.APPROVED, **_scope()
    ).first()
    return row.target_code if row else None


def list_mappings(*, state: str | None = None) -> list[dict[str, Any]]:
    rows = CodeMapping.objects.filter(**_scope())
    if state:
        if state not in MappingState.values:
            raise _bad("state")
        rows = rows.filter(state=state)
    return [mapping_dict(r) for r in rows.order_by("-last_seen_at", "-created_at")[:MAX_LIST]]


def waiting_count() -> int:
    return CodeMapping.objects.filter(
        state__in=(MappingState.UNMAPPED, MappingState.PROPOSED), **_scope()
    ).count()
