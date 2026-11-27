"""Outgoing addresses a customer may send to: an allowlist approved by Zoiko (DEV-384).

GHE-09 SEC-07: no "unbounded internet egress into a healthcare cell"; GHE-13: "webhook endpoint
allowlist" and a security review before a partner is enabled; GHE-22 AT-024: "Prevent connector
from calling non-allowlisted egress host". Until this, any public https address a clinic registered
received its webhooks at once (only private networks were refused, delivery.py).

Rules
- A permit is one exact host name for one customer and one purpose (webhook or connector), kept
  in Zoiko's platform tenant. Never a wildcard.
- Registering a webhook address asks for a permit automatically (proposed by "customer"). Zoiko's
  staff (support R11 or safety R12) approve, refuse or revoke it. When Zoiko's own staff propose a
  permit, a DIFFERENT person approves it.
- Before every send (the verification ping and each delivery) the host must hold an approved
  permit for that customer. ZOIKO_EGRESS decides what happens otherwise: enforce (do not send),
  report (send, and log one line naming only the host and purpose) or off. Production must
  enforce: the release check refuses to start otherwise. An unknown value counts as enforce.
- Revoking stops the next send at once. Every step is audited in the platform's chain.

Every function that touches permits binds the platform tenant itself, so it is safe to call
from inside a customer's own transaction (tenant_transaction nests and puts the customer back).
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction

from foundation.cell_context import require_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.trust import api as trust

from .models import EgressPermit, EgressPurpose, EgressState

log = logging.getLogger("zoiko.egress")

ENFORCE, REPORT, OFF = "enforce", "report", "off"
CUSTOMER = "customer"                    # who proposed a permit asked for by registering an address
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def mode() -> str:
    value = str(getattr(settings, "ZOIKO_EGRESS", ENFORCE)).strip().lower()
    return value if value in (ENFORCE, REPORT, OFF) else ENFORCE


def is_host_name(host: str) -> bool:
    """An exact DNS name (no IP numbers, no wildcards): the only kind a permit can name."""
    return bool(_HOST.match(host))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def _platform():
    found = next((t for t in trust.list_tenants()
                  if t["type"] == "platform" and t["status"] == "active"), None)
    return trust.context_for(found["public_id"]) if found else None


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def permit_dict(p: EgressPermit, labels: dict[str, str] | None = None,
                customers: dict[str, str] | None = None) -> dict[str, Any]:
    names = labels or {}
    return {
        "id": p.public_id, "customer": p.customer_ref,
        "customer_name": (customers or {}).get(p.customer_ref, ""), "host": p.host,
        "purpose": p.purpose, "state": p.state,
        "proposed_by": (p.requested_by_label or "the customer") if p.proposed_by_ref == CUSTOMER
        else names.get(p.proposed_by_ref, p.proposed_by_ref),
        "decided_by": names.get(p.decided_by_ref, p.decided_by_ref) if p.decided_by_ref else "",
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "reason": p.reason, "asked_at": p.created_at.isoformat(),
    }


def _audit(by: str, action: str, p: EgressPermit, reason: str = "") -> None:
    record(actor_ref=by, action=f"egress.{action}", object_type="egress_permit",
           object_ref=p.public_id, outcome=AuditOutcome.ALLOWED, reason_code=reason,
           details={"purpose": p.purpose, "customer_ref": p.customer_ref})


# ------------------------------------------------------------------ asking
def ask(customer_ref: str, host: str, purpose: str, *, proposed_by: str = CUSTOMER,
        label: str = "") -> str:
    """Make sure a permit exists for this customer, host and purpose (a refused or revoked one is
    asked for again). Returns its state. Nothing happens when there is no platform tenant."""
    if purpose not in EgressPurpose.values or not _HOST.match(host):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["host"])
    platform = _platform()
    if platform is None:
        return EgressState.PROPOSED
    with tenant_transaction(platform):
        p = EgressPermit.objects.select_for_update().filter(
            customer_ref=customer_ref, host=host, purpose=purpose, **_scope()).first()
        if p is not None and p.state in (EgressState.PROPOSED, EgressState.APPROVED):
            return str(p.state)
        if p is None:
            try:
                with transaction.atomic():
                    p = EgressPermit.objects.create(
                        customer_ref=customer_ref, host=host, purpose=purpose,
                        proposed_by_ref=proposed_by, requested_by_label=label[:128], **_scope())
            except IntegrityError:                     # asked at the same moment elsewhere
                return EgressState.PROPOSED
        else:
            p.state, p.proposed_by_ref, p.requested_by_label = (
                EgressState.PROPOSED, proposed_by, label[:128])
            p.decided_by_ref, p.decided_at, p.reason = "", None, ""
            p.save()
        _audit(proposed_by if proposed_by != CUSTOMER else "system", "asked", p)
        return str(p.state)


# ------------------------------------------------------------------ Zoiko decides (platform bound)
def decide(permit_ref: str, *, step: str, by: str, reason: object = "",
           clock: Clock | None = None) -> dict[str, Any]:
    """approve | refuse | revoke. Refusing and revoking need a reason. Nobody approves a permit
    they proposed themselves."""
    why = reason.strip() if isinstance(reason, str) else ""
    if step not in ("approve", "refuse", "revoke"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if step != "approve" and not 3 <= len(why) <= 300:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["reason"])
    with transaction.atomic():
        p = EgressPermit.objects.select_for_update().filter(
            public_id=permit_ref, **_scope()).first()
        if p is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        allowed_from = {"approve": (EgressState.PROPOSED,), "refuse": (EgressState.PROPOSED,),
                        "revoke": (EgressState.APPROVED,)}[step]
        if p.state not in allowed_from:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["state"])
        if step == "approve" and p.proposed_by_ref == by:
            raise ZoikoError(ErrorCode.POLICY_DENIED)          # never your own proposal
        p.state = {"approve": EgressState.APPROVED, "refuse": EgressState.REFUSED,
                   "revoke": EgressState.REVOKED}[step]
        p.decided_by_ref, p.decided_at, p.reason = by, utc_now(clock), why
        p.save()
        _audit(by, step, p)
        return permit_dict(p)


def listing(labels: dict[str, str] | None = None,
            customers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every permit (platform tenant bound): waiting ones first, then newest."""
    rows = sorted(EgressPermit.objects.filter(**_scope()),
                  key=lambda p: (p.state != EgressState.PROPOSED, -p.created_at.timestamp()))
    return [permit_dict(p, labels, customers) for p in rows[:300]]


# ------------------------------------------------------------------ before every send
def state_for(customer_ref: str, host: str, purpose: str) -> str:
    """The permit's state for this customer, host and purpose, or "none"."""
    platform = _platform()
    if platform is None:
        return "none"
    with tenant_transaction(platform):
        found = EgressPermit.objects.filter(
            customer_ref=customer_ref, host=host, purpose=purpose, **_scope()
        ).values_list("state", flat=True).first()
    return str(found) if found else "none"


def may_send(customer_ref: str, url: str, purpose: str) -> bool:
    """True when sending is allowed: an approved permit, or gating not enforced (report logs)."""
    current = mode()
    if current == OFF:
        return True
    host = host_of(url)
    if state_for(customer_ref, host, purpose) == EgressState.APPROVED:
        return True
    if current == REPORT:
        log.warning("egress would_refuse purpose=%s host=%s", purpose, host)
        return True
    return False
