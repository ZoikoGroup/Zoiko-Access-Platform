"""Who Zoiko named for what (decided 27 November 2026): the people who each hold a sealed recovery
copy of the master key (DEV-396), and the two people who sign production releases, the CTO and the
Clinical safety lead (DEV-397).

* A support officer (R11) names someone, never themselves. The person must be one of Zoiko's own
  staff logins: the CTO a support officer (R11), the Clinical safety lead a safety officer (R12), a
  key holder either.
* Only the person named confirms. For a key holder that means "I hold the sealed copy, at this
  place"; for a signer, "I accept". Nothing counts until confirmed.
* Withdrawing ends it (by a support officer or by the person themselves), with a reason. One CTO
  and one Clinical safety lead at a time: name a new one only after withdrawing the old.
* Every step is audited. Nothing here stores a key or any part of one: only who holds a copy, of
  which key id, and a short code for where it is sealed.
"""
from __future__ import annotations

import re
from typing import Any

from django.db import IntegrityError, transaction

from foundation import crypto
from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy
from modules.trust import api as trust

from .models import NamedRole, NamedTitle

WRITE = "write:platform"
ATTEST = "attest:platform"
HOLDER_ROLES = {
    NamedTitle.KEY_RECOVERY: frozenset({"R11", "R12"}),
    NamedTitle.CTO: frozenset({"R11"}),
    NamedTitle.CLINICAL_SAFETY_LEAD: frozenset({"R12"}),
}
MIN_KEY_HOLDERS = 2
_ENVELOPE = re.compile(r"^[A-Za-z0-9._:-]{3,40}$")


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def named_dict(n: NamedRole) -> dict[str, Any]:
    return {
        "public_id": n.public_id, "title": n.title, "holder": n.holder_ref, "key_id": n.key_id,
        "envelope_code": n.envelope_code, "appointed_by": n.appointed_by_ref,
        "confirmed_at": n.confirmed_at.isoformat() if n.confirmed_at else None,
        "withdrawn_at": n.withdrawn_at.isoformat() if n.withdrawn_at else None,
        "status": "withdrawn" if n.withdrawn_at else "confirmed" if n.confirmed_at
        else "waiting_for_the_person",
        "created_at": n.created_at.isoformat(),
    }


def _codes(actor_ref: str) -> set[str]:
    try:
        return {code for code, _org in trust.active_roles(actor_ref)}
    except ZoikoError:
        return set()


def appoint(
    *, title: Any, holder_ref: Any, envelope_code: Any = "", actor_public_id: str,
    decision: policy.Decision, organization_ref: str | None,
) -> dict[str, Any]:
    policy.check_receipt(decision, actor_public_id=actor_public_id, operation=WRITE,
                         organization_public_id=organization_ref)
    if decision.role_code != "R11":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if title not in NamedTitle.values:
        raise _bad("title")
    title = NamedTitle(title)
    if not isinstance(holder_ref, str) or holder_ref == actor_public_id:
        raise _bad("holder")                                  # never yourself
    if not _codes(holder_ref) & HOLDER_ROLES[title]:
        raise _bad("holder")                                  # not Zoiko staff of the right kind
    key_id, envelope = "", ""
    if title == NamedTitle.KEY_RECOVERY:
        key_id = crypto.current_key_id()
        envelope = " ".join(str(envelope_code or "").split())
        if not _ENVELOPE.match(envelope):
            raise _bad("envelope_code")
    try:
        with transaction.atomic():
            row = NamedRole.objects.create(
                title=title, holder_ref=holder_ref, key_id=key_id, envelope_code=envelope,
                appointed_by_ref=actor_public_id, **_scope())
    except IntegrityError:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None  # already named, or one at a time
    record(actor_ref=actor_public_id, action="readiness.named", object_type="named_role",
           object_ref=row.public_id, outcome=AuditOutcome.ALLOWED, reason_code=title)
    return named_dict(row)


def _own(ref: str) -> NamedRole:
    found = NamedRole.objects.select_for_update().filter(public_id=ref, **_scope()).first()
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def confirm(*, named_ref: str, actor_public_id: str, clock: Clock | None = None
            ) -> dict[str, Any]:
    """Only the person named. Repeating it is harmless."""
    with transaction.atomic():
        row = _own(named_ref)
        if row.holder_ref != actor_public_id:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if row.withdrawn_at is not None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        if row.confirmed_at is None:
            row.confirmed_at = utc_now(clock)
            row.entity_version += 1
            row.save(update_fields=["confirmed_at", "entity_version", "updated_at"])
            record(actor_ref=actor_public_id, action="readiness.named_confirmed",
                   object_type="named_role", object_ref=row.public_id,
                   outcome=AuditOutcome.ALLOWED, reason_code=row.title)
        return named_dict(row)


def withdraw(*, named_ref: str, actor_public_id: str, decision: policy.Decision | None,
             organization_ref: str | None, clock: Clock | None = None) -> dict[str, Any]:
    """A support officer, or the person themselves."""
    with transaction.atomic():
        row = _own(named_ref)
        if actor_public_id != row.holder_ref:
            if decision is None:
                raise ZoikoError(ErrorCode.POLICY_DENIED)
            policy.check_receipt(decision, actor_public_id=actor_public_id, operation=WRITE,
                                 organization_public_id=organization_ref)
            if decision.role_code != "R11":
                raise ZoikoError(ErrorCode.POLICY_DENIED)
        if row.withdrawn_at is None:
            row.withdrawn_at, row.withdrawn_by_ref = utc_now(clock), actor_public_id
            row.entity_version += 1
            row.save(update_fields=["withdrawn_at", "withdrawn_by_ref", "entity_version",
                                    "updated_at"])
            record(actor_ref=actor_public_id, action="readiness.named_withdrawn",
                   object_type="named_role", object_ref=row.public_id,
                   outcome=AuditOutcome.ALLOWED, reason_code=row.title)
        return named_dict(row)


def list_named() -> list[dict[str, Any]]:
    rows = NamedRole.objects.filter(**_scope()).order_by("title", "-created_at")
    return [named_dict(r) for r in rows]


def _confirmed(title: str) -> Any:
    return NamedRole.objects.filter(title=title, confirmed_at__isnull=False,
                                    withdrawn_at__isnull=True, **_scope())


def is_named(title: str, actor_ref: str) -> bool:
    """Is this person the confirmed holder of this title right now (and still staff of the right
    kind)?"""
    return (_confirmed(title).filter(holder_ref=actor_ref).exists()
            and bool(_codes(actor_ref) & HOLDER_ROLES[NamedTitle(title)]))


def key_holders() -> list[str]:
    """Confirmed holders of a sealed copy of the CURRENT master key, still Zoiko staff."""
    try:
        current = crypto.current_key_id()
    except crypto.CryptoError:
        return []
    refs = set(_confirmed(NamedTitle.KEY_RECOVERY).filter(key_id=current)
               .values_list("holder_ref", flat=True))
    return sorted(r for r in refs if _codes(r) & HOLDER_ROLES[NamedTitle.KEY_RECOVERY])
