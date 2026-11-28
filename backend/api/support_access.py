"""Short-term support access endpoints (DEV-381). The rules are in modules/trust/support_access.py.

Zoiko support (R11 in the platform tenant, with two-step sign-in on):
  GET  platform/support-access                         my requests at every customer
  POST platform/support-access                         {customer, ticket, reason, scopes, minutes}
  POST platform/support-access/<customer>/<id>/end     end my request or grant early
  GET  platform/support-access/<customer>/<id>/<scope> one look, while the grant is active

The customer (staff console; the administrator R13 decides):
  GET  staff/support-access                            every request, and whether I may decide
  POST staff/support-access/<id>/<approve|refuse|end>

What a look shows is built HERE from settings and counts, never from patient records: the support
role's hard prohibitions (person, notes, results, coverage, messages) are not loosened by a grant.
Each look is recorded in the customer's audit chain and the platform's.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import utc_now
from modules.catalog import api as catalog
from modules.evidence import api as evidence
from modules.integration import api as integration
from modules.policy import api as policy
from modules.scheduling import api as scheduling
from modules.trust import api as trust
from modules.waitlist import api as waitlist

from .support import endpoint, json_body, require_recent_second_step
from .views import _authorize, _platform_only, _staff_only

_STEPS = ("approve", "refuse", "end")


# ------------------------------------------------------------------ who may ask, and where
def _support_only(auth: trust.Authenticated) -> str | None:
    """Zoiko support (R11) in the platform tenant, signed in with two-step sign-in."""
    org = _platform_only(auth)
    if "R11" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if not auth.two_step_on:
        raise ZoikoError(ErrorCode.POLICY_DENIED, fields=["two_step"])
    return org


def _customer(customer_id: str) -> dict[str, str]:
    found = next((t for t in trust.list_tenants() if t["public_id"] == customer_id), None)
    if found is None or found["type"] == "platform" or found["status"] != "active":
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return found


def _label(auth: trust.Authenticated) -> str:
    with tenant_transaction():
        return trust.support_requester_label(auth.actor_public_id)


def _platform_record(auth: trust.Authenticated, action: str, ref: str, **details: Any) -> None:
    with tenant_transaction():
        evidence.record(actor_ref=auth.actor_public_id, action=action, object_type="support_access",
                        object_ref=ref, outcome=evidence.AuditOutcome.ALLOWED,
                        details=details or None)


# ------------------------------------------------------------------ support's side
@endpoint("GET", "POST")
def platform_support_access(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _support_only(auth)
    if request.method == "POST":
        _authorize(auth, "write:platform", organization_public_id=org)
        require_recent_second_step(auth)        # asking for access is a risky act (DEV-380)
        body = json_body(request)
        customer = _customer(str(body.get("customer", "")))
        label = _label(auth)
        with tenant_transaction(trust.context_for(customer["public_id"])):
            made = trust.request_support_access(
                requester_ref=auth.actor_public_id, requester_label=label,
                ticket=body.get("ticket"), reason=body.get("reason"),
                scopes=body.get("scopes"), minutes=body.get("minutes"))
        _platform_record(auth, "support.access_requested", made["id"],
                         customer=customer["public_id"])
        return JsonResponse({"access": {**made, "customer": _short(customer)}}, status=201)
    _authorize(auth, "read:platform", organization_public_id=org)
    mine: list[dict[str, Any]] = []
    customers = [t for t in trust.list_tenants() if t["type"] != "platform"]
    for t in customers:
        with tenant_transaction(trust.context_for(t["public_id"])):
            rows = trust.support_access_list(requester_ref=auth.actor_public_id)
        mine.extend({**r, "customer": _short(t)} for r in rows)
    mine.sort(key=lambda r: r["asked_at"], reverse=True)
    return JsonResponse({
        "access": mine,
        "customers": [_short(t) for t in customers if t["status"] == "active"],
        "scopes": [{"code": c, "label": text} for c, text in trust.SUPPORT_SCOPES.items()],
        "minutes": list(trust.SUPPORT_MINUTES),
    })


def _short(t: dict[str, str]) -> dict[str, str]:
    return {"id": t["public_id"], "name": t["name"]}


@endpoint("POST")
def platform_support_access_end(
    request: HttpRequest, auth: trust.Authenticated, customer_id: str, access_id: str,
) -> HttpResponse:
    org = _support_only(auth)
    _authorize(auth, "write:platform", organization_public_id=org)
    customer = _customer(customer_id)
    with tenant_transaction(trust.context_for(customer["public_id"])):
        done = trust.end_support_access(access_id, by=auth.actor_public_id,
                                        requester_ref=auth.actor_public_id)
    _platform_record(auth, "support.access_ended", access_id, customer=customer["public_id"])
    return JsonResponse({"access": {**done, "customer": _short(customer)}})


@endpoint("GET")
def platform_support_access_view(
    request: HttpRequest, auth: trust.Authenticated, customer_id: str, access_id: str, scope: str,
) -> HttpResponse:
    """One look through an ACTIVE grant. A refused look is recorded before it is refused."""
    org = _support_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    require_recent_second_step(auth)
    customer = _customer(customer_id)
    with tenant_transaction(trust.context_for(customer["public_id"])):
        why = trust.use_support_access(access_id, requester_ref=auth.actor_public_id, scope=scope)
    _platform_record(auth, "support.access_used", access_id, customer=customer["public_id"],
                     scope=scope if scope in trust.SUPPORT_SCOPES else "unknown",
                     refused=why or "no")
    if why == "not_found":
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if why:                                   # after both commits: the refusal is on record
        raise ZoikoError(ErrorCode.POLICY_DENIED, fields=[why])
    with tenant_transaction(trust.context_for(customer["public_id"])):
        data = _VIEWS[scope]()
    return JsonResponse({"customer": _short(customer), "scope": scope, "view": data})


# ------------------------------------------------------------------ what a look shows (no patients)
def _setup_view() -> dict[str, Any]:
    directory = trust.staff_directory()
    clinics = []
    for o in directory["organizations"]:
        services = catalog.list_services(active_only=False, organization_ref=o["id"])
        clinics.append({
            "name": o["name"], "status": o["status"],
            "response_target_hours": trust.response_target_hours(o["id"]),
            "opening_hours": trust.business_hours_of(o["id"]),
            "booking_system": trust.booking_system_of(o["id"]),
            "waitlist_offer_hours": waitlist.offer_hours_for(o["id"]),
            "services": [{"name": s["name"], "category": s["category"], "mode": s["mode"],
                          "status": s["status"], "listing_state": s["listing_state"],
                          "duration_minutes": s["duration_minutes"]} for s in services],
        })
    # Staff logins: kind, status and roles only (no login, name or address).
    staff = [{"type": s["type"], "status": s["status"], "pending_invite": s["pending_invite"],
              "roles": sorted({r["role"] for r in s["roles"] if r["status"] == "active"})}
             for s in directory["staff"]]
    return {"clinics": clinics, "staff": staff,
            "staff_without_two_step": trust.staff_without_two_step()}


def _activity_view() -> dict[str, Any]:
    # What happened and how it ended, never who did it or which record (no actor or object ref).
    events = [{"at": e["at"], "action": e["action"], "object_type": e["object"].split(":")[0],
               "outcome": e["outcome"], "reason": e["reason"]}
              for e in evidence.recent_events(100)]
    return {"events": events, "bookings": scheduling.booking_counts(),
            "services": catalog.service_counts(),
            "checks_last_7_days": policy.decision_counts(utc_now() - dt.timedelta(days=7))}


def _deliveries_view() -> dict[str, Any]:
    return dict(integration.support_attempts())


_VIEWS = {"setup": _setup_view, "activity": _activity_view, "deliveries": _deliveries_view}


# ------------------------------------------------------------------ the customer's side
@endpoint("GET")
def staff_support_access(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    _authorize(auth, "read:directory", organization_public_id=org)
    with tenant_transaction():
        rows = trust.support_access_list(labels=trust.actor_labels())
        may = trust.can_decide_support_access(auth.actor_public_id)
    return JsonResponse({
        "access": rows, "can_decide": may,
        "scopes": [{"code": c, "label": text} for c, text in trust.SUPPORT_SCOPES.items()],
    })


@endpoint("POST")
def staff_support_access_step(
    request: HttpRequest, auth: trust.Authenticated, access_id: str, step: str,
) -> HttpResponse:
    """The administrator approves, refuses or ends. Approving is a risky act (the code again)."""
    if step not in _STEPS:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    org = _staff_only(auth)
    if "R13" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "write:staff", organization_public_id=org)
    if step == "approve":
        require_recent_second_step(auth)
    with tenant_transaction():
        if step == "end":
            done = trust.end_support_access(access_id, by=auth.actor_public_id)
        else:
            done = trust.decide_support_access(
                access_id, approve=step == "approve", by=auth.actor_public_id)
        done = next((r for r in trust.support_access_list(labels=trust.actor_labels())
                     if r["id"] == done["id"]), done)
    return JsonResponse({"access": done})
