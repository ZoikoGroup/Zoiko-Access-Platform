"""A patient's own view of, and control over, who can act for them (GHE-06: minimum necessary scope,
revocable, recalculated at every request).

A patient sees every guardian or caregiver who holds access to their record: what they may do, until
when, whether the clinic recorded proof (the KIND only, never the document), and whether the person
is being asked to decide after coming of age. They can NARROW a delegate's access (fewer scopes,
an earlier end date) or END it. They can never WIDEN it: giving access is a clinic's decision after
proof (family.py), because a patient's say-so alone is not enough for every clinical action.

Only the person themselves, through their own login, may do this for their own record.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import transaction

from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import UTC, Clock, utc_now

from .age_review import _own_grant, _require_self, _scope, majority_age
from .directory import actor_labels
from .models import ProxyGrant, ProxyStatus
from .people import revoke_proxy_grant
from .services import _audit, tenant_country

SHOWN = (ProxyStatus.ACTIVE, ProxyStatus.SUSPENDED)
MAX_SCOPES = 12


def _delegate_dict(g: ProxyGrant, labels: dict[str, str], now: dt.datetime) -> dict[str, Any]:
    ran_out = g.valid_until is not None and g.valid_until <= now
    status = ProxyStatus.EXPIRED.value if g.status == ProxyStatus.ACTIVE and ran_out else g.status
    return {
        "id": g.public_id, "who": labels.get(g.delegate.public_id, "") or "A family member",
        "relationship": g.relationship_type, "status": status,
        "scopes": list(g.rights or []), "valid_until": g.valid_until.isoformat()
        if g.valid_until else None, "proof_kind": g.proof_kind,
        "waiting_for_you": g.status == ProxyStatus.SUSPENDED and bool(g.review_reason),
        "review_reason": g.review_reason,
    }


def proxy_person_of(grant_ref: str) -> str:
    """Which patient a guardian grant is about, or "" if there is no such grant here."""
    found = ProxyGrant.objects.filter(public_id=grant_ref, **_scope()).values_list(
        "person__public_id", flat=True).first()
    return found or ""


def access_overview(person_ref: str, *, clock: Clock | None = None) -> dict[str, Any]:
    """Who may act for this person right now. Runs in the bound tenant; the caller has been allowed
    to read exactly this person."""
    now = utc_now(clock)
    grants = list(ProxyGrant.objects.select_related("delegate", "person").filter(
        person__public_id=person_ref, status__in=SHOWN, **_scope()).order_by("created_at"))
    labels = actor_labels()
    return {
        "delegates": [_delegate_dict(g, labels, now) for g in grants],
        "age_of_majority": majority_age(tenant_country(_scope()["tenant_id"])),
    }


def narrow_access(
    *, grant_ref: str, actor_public_id: str, scopes: list[str] | None = None,
    valid_until: str = "", clock: Clock | None = None,
) -> ProxyGrant:
    """Reduce a delegate's access: keep only some of their scopes, and/or end it sooner. Never more.
    Taking away every scope ends the access."""
    now = utc_now(clock)
    if scopes is None and not (valid_until or "").strip():
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["scopes"])
    with transaction.atomic():
        grant = _own_grant(grant_ref)
        _require_self(grant, actor_public_id)
        if grant.status not in SHOWN:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        changed: list[str] = []
        if scopes is not None:
            if (not isinstance(scopes, list) or len(scopes) > MAX_SCOPES
                    or not all(isinstance(s, str) for s in scopes)):
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["scopes"])
            kept = [s for s in dict.fromkeys(scopes)]
            if not set(kept) <= set(grant.rights or []):
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["scopes"])  # never widen
            if not kept:
                ended = revoke_proxy_grant(grant.public_id, by=actor_public_id, clock=clock)
                ProxyGrant.objects.filter(pk=grant.pk).update(review_reason="")
                ended.refresh_from_db()
                return ended
            if kept != list(grant.rights or []):
                grant.rights = kept
                changed.append("scopes")
        if (valid_until or "").strip():
            try:
                day = dt.date.fromisoformat(valid_until.strip())
            except ValueError:
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["valid_until"]) from None
            until = dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=UTC)
            if until <= now or (grant.valid_until is not None and until >= grant.valid_until):
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["valid_until"])
            grant.valid_until = until
            changed.append("valid_until")
        if not changed:
            return grant                                           # nothing to narrow: harmless
        grant.entity_version += 1
        grant.save(update_fields=["rights", "valid_until", "entity_version", "updated_at"])
        _audit(actor_public_id, "proxy.narrowed", "proxy_grant", grant.public_id,
               reason="+".join(changed))
        return grant
