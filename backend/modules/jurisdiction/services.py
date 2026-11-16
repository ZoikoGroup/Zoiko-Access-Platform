"""Country packs and their review (GHE-04 W02, W05).

Only Zoiko's own platform staff can use this. The steps and who may take them:

    draft --validate--> validated --submit--> expert_review --both approvals--> signed
       ^                    |                       |
       +------ reject ------+-----------------------+            signed --suspend--> suspended
                                                                signed|suspended --retire--> retired

* A pack is checked (`validate`) only when nothing required is missing: evidence for legal, privacy
  and security, a clinical status, and an expiry date.
* Two different people attest to the exact content: a support officer (R11, standing in for legal
  counsel until such a role exists) and a safety officer (R12, standing in for the clinical lead).
  Neither may be the author, and they may not be the same person. Changing a pack clears them.
* Nothing here can make a pack live (DEV-128). "Signed" means "approved on record".
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

from .manifest import check_identity, fingerprint, normalize, ready_to_sign
from .models import CountryPack, PackApproval, PackStatus

WRITE = "write:platform"
ATTEST = "attest:platform"
MAX_NOTE = 300
APPROVAL_ROLES = {"legal": "R11", "clinical": "R12"}      # which role may give which approval


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _own(ref: str) -> CountryPack:
    found = CountryPack.objects.select_for_update().filter(public_id=ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def _expired(pack: CountryPack, today: dt.date) -> bool:
    expires = (pack.manifest.get("validity") or {}).get("expires_on")
    return bool(expires) and dt.date.fromisoformat(str(expires)) < today


def pack_dict(pack: CountryPack, clock: Clock | None = None) -> dict[str, Any]:
    approvals = {a.role: a for a in PackApproval.objects.filter(pack=pack, **_scope())}
    today = utc_now(clock).date()
    return {
        "public_id": pack.public_id, "pack_ref": pack.pack_ref, "country": pack.country,
        "service": pack.service, "revision": pack.revision, "status": pack.status,
        "manifest": pack.manifest, "content_hash": pack.content_hash,
        "author": pack.author_ref, "note": pack.note, "created_at": pack.created_at.isoformat(),
        "approvals": {r: {"by": a.approver_ref, "at": a.created_at.isoformat()}
                      for r, a in approvals.items()},
        "missing": ready_to_sign(pack.manifest) if pack.status in (
            PackStatus.DRAFT, PackStatus.VALIDATED) else [],
        "expired": pack.status == PackStatus.SIGNED and _expired(pack, today),
    }


def _clean_note(value: str, low: int = 0) -> str:
    text = " ".join((value or "").split())
    if len(text) > MAX_NOTE or len(text) < low or any(ord(ch) < 32 for ch in text):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["note"])
    return text


def _check(decision: policy.Decision, actor: str, operation: str, org: str | None) -> None:
    policy.check_receipt(decision, actor_public_id=actor, operation=operation,
                         organization_public_id=org)


def _audit(actor: str, action: str, pack: CountryPack, outcome: AuditOutcome = AuditOutcome.ALLOWED
           ) -> None:
    record(actor_ref=actor, action=f"jurisdiction.{action}", object_type="country_pack",
           object_ref=pack.public_id, outcome=outcome, reason_code=pack.status)


def _rehash(pack: CountryPack) -> None:
    pack.content_hash = fingerprint(pack.pack_ref, pack.country, pack.service, pack.revision,
                                    pack.manifest)


def create_pack(
    *, pack_ref: str, country: str, service: str, manifest: Any, actor_public_id: str,
    decision: policy.Decision, organization_ref: str | None,
) -> dict[str, Any]:
    _check(decision, actor_public_id, WRITE, organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    check_identity(pack_ref, country, service)
    clean = normalize(manifest or {})
    with transaction.atomic():
        if CountryPack.objects.filter(pack_ref=pack_ref, **_scope()).exists():
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row = CountryPack(pack_ref=pack_ref, country=country, service=service, manifest=clean,
                          author_ref=actor_public_id, **_scope())
        _rehash(row)
        row.save()
        _audit(actor_public_id, "created", row)
        return pack_dict(row)


def edit_pack(
    *, pack_ref: str, manifest: Any, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """Only a draft can change (a changed pack would no longer match anyone's approval)."""
    _check(decision, actor_public_id, WRITE, organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    clean = normalize(manifest or {})
    with transaction.atomic():
        row = _own(pack_ref)
        if row.status != PackStatus.DRAFT:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        row.manifest = clean
        _rehash(row)
        row.entity_version += 1
        row.save(update_fields=["manifest", "content_hash", "entity_version", "updated_at"])
        _audit(actor_public_id, "edited", row)
        return pack_dict(row)


def _move(row: CountryPack, status: str, actor: str, action: str, note: str = "") -> None:
    row.status = status
    if note or action in ("reject", "suspend"):
        row.note = note
    row.entity_version += 1
    row.save(update_fields=["status", "note", "entity_version", "updated_at"])
    _audit(actor, action, row)


def validate(
    *, pack_ref: str, actor_public_id: str, decision: policy.Decision, organization_ref: str | None,
) -> dict[str, Any]:
    _check(decision, actor_public_id, WRITE, organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with transaction.atomic():
        row = _own(pack_ref)
        if row.status != PackStatus.DRAFT:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        missing = ready_to_sign(row.manifest)
        if missing:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=missing)
        _move(row, PackStatus.VALIDATED, actor_public_id, "validated")
        return pack_dict(row)


def submit(
    *, pack_ref: str, actor_public_id: str, decision: policy.Decision, organization_ref: str | None,
) -> dict[str, Any]:
    _check(decision, actor_public_id, WRITE, organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with transaction.atomic():
        row = _own(pack_ref)
        if row.status != PackStatus.VALIDATED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        _move(row, PackStatus.EXPERT_REVIEW, actor_public_id, "submitted")
        return pack_dict(row)


def reject(
    *, pack_ref: str, note: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """A reviewer (support or safety officer) sends it back for fixing. Approvals are cleared."""
    _check(decision, actor_public_id, ATTEST if decision.role_code == "R12" else WRITE,
           organization_ref)
    if decision.role_code not in ("R11", "R12"):
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    reason = _clean_note(note, low=3)
    with transaction.atomic():
        row = _own(pack_ref)
        if row.status not in (PackStatus.VALIDATED, PackStatus.EXPERT_REVIEW):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        PackApproval.objects.filter(pack=row, **_scope()).delete()
        _move(row, PackStatus.DRAFT, actor_public_id, "reject", reason)
        return pack_dict(row)


def approve(
    *, pack_ref: str, role: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """One of the two attestations, bound to the exact content. When both are in, it is signed."""
    if role not in APPROVAL_ROLES:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["role"])
    _check(decision, actor_public_id, ATTEST, organization_ref)
    if decision.role_code != APPROVAL_ROLES[role]:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with transaction.atomic():
        row = _own(pack_ref)
        if row.status != PackStatus.EXPERT_REVIEW:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if actor_public_id == row.author_ref:
            raise ZoikoError(ErrorCode.POLICY_DENIED)          # nobody approves their own work
        existing = list(PackApproval.objects.filter(pack=row, **_scope()))
        if any(a.role == role for a in existing):
            return pack_dict(row)                               # repeating it is harmless
        if any(a.approver_ref == actor_public_id for a in existing):
            raise ZoikoError(ErrorCode.POLICY_DENIED)          # two different people
        PackApproval(pack=row, role=role, approver_ref=actor_public_id,
                     content_hash=row.content_hash, **_scope()).save()
        record(actor_ref=actor_public_id, action=f"jurisdiction.approved_{role}",
               object_type="country_pack", object_ref=row.public_id, outcome=AuditOutcome.ALLOWED)
        if len(existing) + 1 == len(APPROVAL_ROLES):
            _move(row, PackStatus.SIGNED, actor_public_id, "signed")
        return pack_dict(row)


def suspend(
    *, pack_ref: str, note: str, actor_public_id: str, decision: policy.Decision,
    organization_ref: str | None,
) -> dict[str, Any]:
    """Stopping is easier than starting: either a support or a safety officer may suspend."""
    _check(decision, actor_public_id, ATTEST if decision.role_code == "R12" else WRITE,
           organization_ref)
    if decision.role_code not in ("R11", "R12"):
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    reason = _clean_note(note, low=3)
    with transaction.atomic():
        row = _own(pack_ref)
        if row.status == PackStatus.SUSPENDED:
            return pack_dict(row)
        if row.status != PackStatus.SIGNED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        _move(row, PackStatus.SUSPENDED, actor_public_id, "suspend", reason)
        return pack_dict(row)


def retire(
    *, pack_ref: str, actor_public_id: str, decision: policy.Decision, organization_ref: str | None,
) -> dict[str, Any]:
    _check(decision, actor_public_id, WRITE, organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with transaction.atomic():
        row = _own(pack_ref)
        if row.status == PackStatus.RETIRED:
            return pack_dict(row)
        if row.status not in (PackStatus.SIGNED, PackStatus.SUSPENDED):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        _move(row, PackStatus.RETIRED, actor_public_id, "retired")
        return pack_dict(row)


def revalidate(
    *, pack_ref: str, actor_public_id: str, decision: policy.Decision, organization_ref: str | None,
) -> dict[str, Any]:
    """A suspended pack comes back only as a NEW revision, as a draft (the old one is retired)."""
    _check(decision, actor_public_id, WRITE, organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with transaction.atomic():
        old = _own(pack_ref)
        if old.status != PackStatus.SUSPENDED:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        newer = CountryPack(
            pack_ref=old.pack_ref, country=old.country, service=old.service,
            revision=old.revision + 1, manifest=old.manifest, author_ref=actor_public_id,
            **_scope())
        _rehash(newer)
        newer.save()
        _move(old, PackStatus.RETIRED, actor_public_id, "retired")
        _audit(actor_public_id, "revalidated", newer)
        return pack_dict(newer)


def data_request_days(country: str, clock: Clock | None = None) -> tuple[int, str] | None:
    """The data-request deadline in days set by a SIGNED, unexpired pack for this country, with the
    pack and revision it came from, or None when no such pack exists. If several disagree, the
    strictest (smallest) wins. Call with the PLATFORM tenant bound: packs live there (DEV-133)."""
    if not country:
        return None
    today = utc_now(clock).date()
    found = [
        (p.manifest["settings"]["data_request_days"], f"{p.pack_ref}@{p.revision}")
        for p in CountryPack.objects.filter(country=country, status=PackStatus.SIGNED, **_scope())
        if p.manifest["settings"]["data_request_days"] and not _expired(p, today)
    ]
    return min(found) if found else None


def pack_covers(
    country: str, capabilities: list[str], clock: Clock | None = None
) -> str | None:
    """`pack@revision` of a SIGNED, unexpired pack for this country that switches on EVERY one of
    these capabilities, or None. Call with the platform tenant bound (DEV-137)."""
    today = utc_now(clock).date()
    for p in CountryPack.objects.filter(
            country=country, status=PackStatus.SIGNED, **_scope()
    ).order_by("pack_ref", "-revision"):
        if not _expired(p, today) and all(p.manifest["capabilities"].get(c) for c in capabilities):
            return f"{p.pack_ref}@{p.revision}"
    return None


def enabled_capabilities(country: str, clock: Clock | None = None) -> frozenset[str]:
    """Every capability switched on by at least one SIGNED, unexpired, unsuspended pack for this
    country (a suspended or draft pack counts for nothing). Call with the platform tenant bound
    (DEV-156)."""
    if not country:
        return frozenset()
    today = utc_now(clock).date()
    on: set[str] = set()
    for p in CountryPack.objects.filter(country=country, status=PackStatus.SIGNED, **_scope()):
        if not _expired(p, today):
            on |= {name for name, value in p.manifest["capabilities"].items() if value}
    return frozenset(on)


def list_packs(clock: Clock | None = None) -> list[dict[str, Any]]:
    rows = CountryPack.objects.filter(**_scope()).order_by("country", "pack_ref", "-revision")
    return [pack_dict(r, clock) for r in rows]


def get_pack(ref: str) -> dict[str, Any]:
    found = CountryPack.objects.filter(public_id=ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return pack_dict(found)

