"""Short-term support access (DEV-381, GHE-05 W10, GHE-09 T04, GHE-15 W06).

Zoiko's support staff have NO standing access inside a customer. To troubleshoot they ask for it:
a ticket reference, a reason, which views they need, and for how long (10, 20 or 30 minutes). The
customer's administrator approves or refuses; the clock starts only at approval. Either side can
end it early. Every look is recorded in the CUSTOMER's audit chain (and the platform's), and a
look after the end is refused and recorded too.

What a grant opens is setup and status only, never a patient (the support role's hard
prohibitions stay in force: api/support_access.py builds each view from counts and settings).

Every function here runs inside the CUSTOMER's tenant transaction.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.mail import queue_email
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .display_names import display_name_of
from .models import Credential, SupportAccess, SupportAccessState
from .services import active_roles, organization_names, verified_emails_for_role

# What a grant may open. Each is a read-only view without patient data.
SCOPES: dict[str, str] = {
    "setup": "Clinics, services, opening hours, response targets and staff logins (roles and "
             "status only: no names or addresses)",
    "activity": "Recent audit entries (what happened and the outcome, without who or what "
                "record) and booking counts",
    "deliveries": "Messages to partner systems: counts and stuck deliveries (status only)",
}
MINUTES = (10, 20, 30)          # example lengths from GHE-05 W10, pending risk review
ASK_EXPIRES_AFTER = dt.timedelta(hours=24)   # an unanswered request lapses
_TICKET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._#/-]{1,39}$")

ACTIVE, EXPIRED, LAPSED = "active", "expired", "lapsed"   # computed states (never stored)


def _now(clock: Clock | None) -> dt.datetime:
    return utc_now(clock)


def state_of(row: SupportAccess, now: dt.datetime) -> str:
    """requested | lapsed | active | expired | refused | ended."""
    if row.state == SupportAccessState.REQUESTED:
        return LAPSED if now - row.created_at > ASK_EXPIRES_AFTER else "requested"
    if row.state == SupportAccessState.APPROVED:
        return ACTIVE if row.ends_at is not None and now < row.ends_at else EXPIRED
    return str(row.state)


def as_dict(
    row: SupportAccess, now: dt.datetime, labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    names = labels or {}
    return {
        "id": row.public_id, "requester": row.requester_label, "ticket": row.ticket_ref,
        "reason": row.reason, "scopes": list(row.scopes), "minutes": row.minutes,
        "state": state_of(row, now), "asked_at": row.created_at.isoformat(),
        "decided_by": (names.get(row.decided_by_ref, row.decided_by_ref)
                       if row.decided_by_ref else ""),
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "ends_at": row.ends_at.isoformat() if row.ends_at else None,
        "ended_by": _ended_by(row, names),
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "uses": row.uses,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


def _ended_by(row: SupportAccess, names: dict[str, str]) -> str:
    if not row.ended_by_ref:
        return ""
    if row.ended_by_ref == row.requester_ref:
        return row.requester_label
    return names.get(row.ended_by_ref, row.ended_by_ref)


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _audit(by: str, action: str, row: SupportAccess, outcome: AuditOutcome = AuditOutcome.ALLOWED,
           reason: str = "", **details: Any) -> None:
    record(actor_ref=by, action=action, object_type="support_access", object_ref=row.public_id,
           outcome=outcome, reason_code=reason, details=details or None)


def _admin_emails() -> list[str]:
    found: set[str] = set()
    for org in organization_names():
        found.update(verified_emails_for_role(org, "R13"))
    return sorted(found)


def requester_label(actor_public_id: str) -> str:
    """How the customer sees the person asking: their login, with their name when they set one.
    Runs in ZOIKO's platform tenant (where the requester's login lives)."""
    cred = Credential.objects.filter(actor__public_id=actor_public_id, **_scope()).first()
    login = cred.login if cred is not None else actor_public_id
    name = display_name_of(actor_public_id)
    return f"{name} ({login})" if name else login


# ------------------------------------------------------------------ support asks
def request(
    *, requester_ref: str, requester_label: str, ticket: object, reason: object,
    scopes: object, minutes: object, clock: Clock | None = None,
) -> dict[str, Any]:
    """Ask this customer for access. One open request or grant per person per customer."""
    bad: list[str] = []
    ticket_ref = ticket.strip() if isinstance(ticket, str) else ""
    if not _TICKET.match(ticket_ref):
        bad.append("ticket")
    why = reason.strip() if isinstance(reason, str) else ""
    if not 10 <= len(why) <= 300:
        bad.append("reason")
    wanted = sorted(set(scopes)) if isinstance(scopes, list) and all(
        isinstance(s, str) for s in scopes) else []
    if not wanted or any(s not in SCOPES for s in wanted):
        bad.append("scopes")
    if minutes not in MINUTES or isinstance(minutes, bool):
        bad.append("minutes")
    if bad:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=bad)
    now = _now(clock)
    with transaction.atomic():
        mine = SupportAccess.objects.select_for_update().filter(
            requester_ref=requester_ref, **_scope())
        if any(state_of(r, now) in ("requested", ACTIVE) for r in mine):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["open_request"])
        row = SupportAccess.objects.create(
            requester_ref=requester_ref, requester_label=requester_label[:128],
            ticket_ref=ticket_ref, reason=why, scopes=wanted, minutes=int(minutes))  # type: ignore[call-overload]
        _audit(requester_ref, "support.access_requested", row, scopes=",".join(wanted),
               minutes=row.minutes)
        for address in _admin_emails():
            queue_email(kind="support_access_requested", to_address=address, values={
                "requester": row.requester_label, "minutes": str(row.minutes),
                "ticket": row.ticket_ref, "scopes": ", ".join(wanted)})
        return as_dict(row, now)


# ------------------------------------------------------------------ the customer decides
def _get(grant_ref: str, *, lock: bool = True) -> SupportAccess:
    rows = SupportAccess.objects.filter(public_id=grant_ref, **_scope())
    row = (rows.select_for_update() if lock else rows).first()
    if row is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return row


def decide(grant_ref: str, *, approve: bool, by: str, clock: Clock | None = None) -> dict[str, Any]:
    """The customer's administrator approves (the clock starts now) or refuses."""
    now = _now(clock)
    with transaction.atomic():
        row = _get(grant_ref)
        if state_of(row, now) != "requested":
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["state"])
        row.decided_by_ref, row.decided_at = by, now
        if approve:
            row.state, row.ends_at = SupportAccessState.APPROVED, now + dt.timedelta(
                minutes=row.minutes)
        else:
            row.state = SupportAccessState.REFUSED
        row.save()
        _audit(by, "support.access_approved" if approve else "support.access_refused", row)
        return as_dict(row, now)


def end(grant_ref: str, *, by: str, requester_ref: str = "",
        clock: Clock | None = None) -> dict[str, Any]:
    """End a request or an active grant early. Support may end only their own (`requester_ref`)."""
    now = _now(clock)
    with transaction.atomic():
        row = _get(grant_ref)
        if requester_ref and row.requester_ref != requester_ref:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if state_of(row, now) not in ("requested", ACTIVE):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["state"])
        row.state, row.ended_by_ref, row.ended_at = SupportAccessState.ENDED, by, now
        if row.ends_at is not None and row.ends_at > now:
            row.ends_at = now
        row.save()
        _audit(by, "support.access_ended", row)
        return as_dict(row, now)


def listing(clock: Clock | None = None, *, requester_ref: str = "",
            labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Newest first: every request of this customer, or only one requester's."""
    now = _now(clock)
    rows = SupportAccess.objects.filter(**_scope())
    if requester_ref:
        rows = rows.filter(requester_ref=requester_ref)
    return [as_dict(r, now, labels) for r in rows.order_by("-created_at")[:100]]


def can_decide(actor_public_id: str) -> bool:
    return any(role == "R13" for role, _ in active_roles(actor_public_id))


# ------------------------------------------------------------------ support looks
def use(grant_ref: str, *, requester_ref: str, scope: str, clock: Clock | None = None) -> str:
    """Record one look. "" when allowed; otherwise why not (requested, lapsed, expired, refused,
    ended, scope, not_found). A refusal is RECORDED too: the caller must let this transaction
    commit before refusing the request."""
    now = _now(clock)
    with transaction.atomic():
        row = SupportAccess.objects.select_for_update().filter(
            public_id=grant_ref, requester_ref=requester_ref, **_scope()).first()
        if row is None:
            return "not_found"
        state = state_of(row, now)
        why = "" if state == ACTIVE else state
        if not why and scope not in row.scopes:
            why = "scope"
        if why:
            _audit(requester_ref, "support.access_used", row, AuditOutcome.DENIED, why,
                   scope=scope if scope in SCOPES else "unknown")
            return why
        row.uses += 1
        row.last_used_at = now
        row.save(update_fields=["uses", "last_used_at", "updated_at"])
        _audit(requester_ref, "support.access_used", row, scope=scope)
        return ""
