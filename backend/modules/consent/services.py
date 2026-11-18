"""Recording and reading a patient's consent (see models.py).

The caller has already decided that this actor may speak for this patient (the policy engine, in the
API layer); this module records the choice, keeps its history, and answers `is_granted`.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import IntegrityError, transaction

from foundation.cell_context import require_context
from foundation.chain import GENESIS, chain_digest, verify_chain
from foundation.errors import ErrorCode, ZoikoError
from foundation.outbox import emit
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import ConsentEvent, ConsentGrant, ConsentState

# What a patient may be asked to share, and which right the policy engine checks for it. A purpose
# is listed here only when something real reads the choice.
PURPOSES: dict[str, dict[str, str]] = {
    "insurance_details": {
        "resource": "coverage",
        "label": "Let this clinic's staff see my insurance details",
    },
}
AUDIENCES = ("clinic_staff",)
DEFAULT_AUDIENCE = "clinic_staff"
MAX_DAYS = 3650


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _effective(row: ConsentGrant, now: dt.datetime) -> bool:
    return (row.state == ConsentState.GRANTED
            and (row.expires_at is None or row.expires_at > now))


def states(
    person_refs: list[str], purpose: str, audience: str = DEFAULT_AUDIENCE,
    clock: Clock | None = None,
) -> dict[str, bool]:
    """For each person who HAS made a choice about this purpose: is it a yes right now? A person who
    has made no choice is absent, so a caller can fall back to what it knew before. One query."""
    if not person_refs:
        return {}
    now = utc_now(clock)
    rows = ConsentGrant.objects.filter(
        person_ref__in=person_refs, purpose=purpose, audience=audience, **_scope())
    return {r.person_ref: _effective(r, now) for r in rows}


def is_granted(
    person_ref: str, purpose: str, audience: str = DEFAULT_AUDIENCE, clock: Clock | None = None
) -> bool:
    """Has this patient said yes, and has that yes not run out? No choice means no."""
    return states([person_ref], purpose, audience, clock).get(person_ref, False)


def _expiry(days: Any, now: dt.datetime) -> dt.datetime | None:
    if days is None:
        return None
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise _bad("days")
    return now + dt.timedelta(days=days)


def _parts(row: ConsentEvent) -> list[Any]:
    return [row.consent.public_id, row.kind, row.actor_ref, row.occurred_at.isoformat(),
            row.expires_at.isoformat() if row.expires_at else ""]


def set_consent(
    *, person_ref: str, purpose: str, grant: bool, actor_ref: str, days: Any = None,
    audience: str = DEFAULT_AUDIENCE, clock: Clock | None = None,
) -> dict[str, Any]:
    """Record a yes (optionally for a number of days) or a no. Saying a plain "no" again, or a plain
    "yes until withdrawn" again, changes nothing and adds nothing to the history. A yes with a
    number of days is always a new event, because its end date moves."""
    if purpose not in PURPOSES:
        raise _bad("purpose")
    if audience not in AUDIENCES:
        raise _bad("audience")
    if not isinstance(grant, bool):
        raise _bad("grant")
    now = utc_now(clock)
    expires = _expiry(days, now) if grant else None
    wanted = ConsentState.GRANTED if grant else ConsentState.WITHDRAWN
    with transaction.atomic():
        try:
            row, _ = ConsentGrant.objects.select_for_update().get_or_create(
                person_ref=person_ref, purpose=purpose, audience=audience,
                defaults={"changed_by_ref": actor_ref}, **_scope())
        except IntegrityError:                                  # two first choices at once
            row = ConsentGrant.objects.select_for_update().get(
                person_ref=person_ref, purpose=purpose, audience=audience, **_scope())
        has_history = ConsentEvent.objects.filter(consent=row, **_scope()).exists()
        same = row.state == wanted and has_history and (
            (not grant) or (expires is None and row.expires_at is None))
        if same:
            return consent_dict(row, now)
        row.state = wanted
        row.expires_at = expires
        row.changed_by_ref = actor_ref
        if grant:
            row.granted_at = now
        row.entity_version += 1
        row.save()
        last = ConsentEvent.objects.filter(consent=row, **_scope()).order_by("-seq").first()
        seq = last.seq + 1 if last else 1
        prev = last.record_digest if last else GENESIS
        kind = "granted" if grant else "withdrawn"
        digest = chain_digest(prev, seq, row.public_id, kind, actor_ref, now.isoformat(),
                              expires.isoformat() if expires else "")
        ConsentEvent.objects.create(
            consent=row, seq=seq, kind=kind, actor_ref=actor_ref, occurred_at=now,
            expires_at=expires, prev_digest=prev, record_digest=digest, **_scope())
        record(actor_ref=actor_ref, action=f"consent.{kind}", object_type="consent",
               object_ref=row.public_id, outcome=AuditOutcome.ALLOWED, reason_code=purpose)
        emit(event_type="consent.changed.v1", aggregate_type="consent", aggregate_ref=row.public_id,
             payload={"consent_ref": row.public_id, "purpose": purpose, "state": row.state})
        return consent_dict(row, now)


def consent_dict(row: ConsentGrant, now: dt.datetime) -> dict[str, Any]:
    live = _effective(row, now)
    ran_out = row.state == ConsentState.GRANTED and not live
    return {
        "purpose": row.purpose, "audience": row.audience, "label": PURPOSES[row.purpose]["label"],
        "state": "expired" if ran_out else row.state, "shared": live,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "changed_at": row.updated_at.isoformat(), "changed_by": row.changed_by_ref,
    }


def list_for_person(person_ref: str, clock: Clock | None = None) -> list[dict[str, Any]]:
    """Every purpose this patient can choose about, with their current choice (or "not shared")."""
    now = utc_now(clock)
    rows = {r.purpose: r for r in ConsentGrant.objects.filter(
        person_ref=person_ref, audience=DEFAULT_AUDIENCE, **_scope())}
    out = []
    for purpose, meta in PURPOSES.items():
        if purpose in rows:
            out.append(consent_dict(rows[purpose], now))
        else:
            out.append({"purpose": purpose, "audience": DEFAULT_AUDIENCE, "label": meta["label"],
                        "state": ConsentState.WITHDRAWN.value, "shared": False,
                        "expires_at": None, "changed_at": None, "changed_by": ""})
    return out


def history(person_ref: str, purpose: str) -> dict[str, Any]:
    """Every change to one consent, oldest first, and whether the chain still checks out."""
    row = ConsentGrant.objects.filter(
        person_ref=person_ref, purpose=purpose, audience=DEFAULT_AUDIENCE, **_scope()).first()
    if row is None:
        return {"events": [], "chain_ok": True}
    events = list(ConsentEvent.objects.select_related("consent").filter(
        consent=row, **_scope()).order_by("seq"))
    ok, _ = verify_chain(events, _parts)
    return {"chain_ok": ok, "events": [
        {"seq": e.seq, "kind": e.kind, "by": e.actor_ref, "at": e.occurred_at.isoformat(),
         "expires_at": e.expires_at.isoformat() if e.expires_at else None} for e in events]}


def purpose_resource(purpose: str) -> str:
    """The policy resource class that guards this purpose (so the API asks the right question)."""
    if purpose not in PURPOSES:
        raise _bad("purpose")
    return PURPOSES[purpose]["resource"]
