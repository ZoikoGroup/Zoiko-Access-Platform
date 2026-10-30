"""DEV-ONLY console: a page and a small JSON API to try the real operations by hand.

It calls the same public module APIs real code will use, so what you see is what the system
does. It exists only while DEBUG and DEV_AUTH_STUB are both on (see config/urls.py), has no
login, and must never be reachable in production.
"""
from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import CellContext, bind_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.catalog import api as catalog
from modules.evidence import api as evidence
from modules.policy import api as policy
from modules.scheduling import api as scheduling
from modules.trust import api as trust

_PAGE = Path(__file__).with_name("page.html")
_BY = "dev_console"


def _guard() -> None:
    if not (settings.DEBUG and getattr(settings, "DEV_AUTH_STUB", False)):
        raise Http404


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED) from None
    if not isinstance(data, dict):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED)
    return data


def _text(body: dict[str, Any], key: str, required: bool = True) -> str:
    value = body.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[key])
    return value.strip()


def _enum(enum: Any, body: dict[str, Any], key: str) -> Any:
    try:
        return enum(_text(body, key))
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[key]) from None


# ---- actions that run inside a tenant: (body) -> result -------------------------------
def _create_org(b: dict[str, Any]) -> dict[str, Any]:
    return {"public_id": trust.create_organization(name=_text(b, "name"), by=_BY).public_id}


def _register_actor(b: dict[str, Any]) -> dict[str, Any]:
    actor = trust.register_actor(
        actor_type=_enum(trust.ActorType, b, "actor_type"),
        issuer=_text(b, "issuer"), subject=_text(b, "subject"), by=_BY,
    )
    return {"public_id": actor.public_id}


def _assign_role(b: dict[str, Any]) -> dict[str, Any]:
    a = trust.assign_role(
        actor_public_id=_text(b, "actor"), organization_public_id=_text(b, "organization"),
        role_code=_text(b, "role_code"), by=_BY,
    )
    return {"public_id": a.public_id}


def _end_assignment(b: dict[str, Any]) -> dict[str, Any]:
    a = trust.end_assignment(_text(b, "assignment"), reason=_text(b, "reason"), by=_BY)
    return {"public_id": a.public_id, "status": a.status}


def _set_status(b: dict[str, Any]) -> dict[str, Any]:
    actor = trust.set_actor_status(_text(b, "actor"), _enum(trust.Status, b, "status"), by=_BY)
    return {"public_id": actor.public_id, "status": actor.status}


def _decide(b: dict[str, Any]) -> dict[str, Any]:
    d = policy.decide(
        actor_public_id=_text(b, "actor"), operation=_text(b, "operation"),
        purpose_code=_text(b, "purpose"),
        organization_public_id=_text(b, "organization", False) or None,
        subject_ref=_text(b, "subject", False) or None,
    )
    return {"allowed": d.allowed, "reason": d.reason.value, "role": d.role_code,
            "decision": d.decision_public_id}


def _self_registration(b: dict[str, Any]) -> dict[str, Any]:
    enabled = b.get("enabled")
    if not isinstance(enabled, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["enabled"])
    trust.set_self_registration(enabled, by=_BY)
    return {"enabled": enabled}


def _set_password(b: dict[str, Any]) -> dict[str, Any]:
    actor = _text(b, "actor")
    trust.set_password(actor, login=_text(b, "login"), password=_text(b, "password"), by=_BY)
    return {"public_id": actor}


def _create_person(b: dict[str, Any]) -> dict[str, Any]:
    return {"public_id": trust.create_person(by=_BY).public_id}


def _link_person(b: dict[str, Any]) -> dict[str, Any]:
    p = trust.link_person_to_actor(_text(b, "person"), _text(b, "actor"), by=_BY)
    return {"public_id": p.public_id}


def _care_start(b: dict[str, Any]) -> dict[str, Any]:
    link = trust.start_care_relationship(
        actor_public_id=_text(b, "actor"), person_public_id=_text(b, "person"),
        basis=_enum(trust.CareBasis, b, "basis"), by=_BY,
    )
    return {"public_id": link.public_id}


def _care_end(b: dict[str, Any]) -> dict[str, Any]:
    return {"public_id": trust.end_care_relationship(_text(b, "link"), by=_BY).public_id}


def _care_contest(b: dict[str, Any]) -> dict[str, Any]:
    link = trust.set_care_contested(_text(b, "link"), bool(b.get("contested")), by=_BY)
    return {"public_id": link.public_id, "contested": link.contested}


def _proxy_propose(b: dict[str, Any]) -> dict[str, Any]:
    g = trust.propose_proxy_grant(
        delegate_public_id=_text(b, "delegate"), person_public_id=_text(b, "person"),
        relationship_type=_text(b, "relationship_type"), by=_BY,
    )
    return {"public_id": g.public_id}


def _proxy_approve(b: dict[str, Any]) -> dict[str, Any]:
    g = trust.approve_proxy_grant(_text(b, "grant"), _text(b, "approver"))
    return {"public_id": g.public_id, "status": g.status}


def _proxy_revoke(b: dict[str, Any]) -> dict[str, Any]:
    g = trust.revoke_proxy_grant(_text(b, "grant"), by=_BY)
    return {"public_id": g.public_id, "status": g.status}


# Catalog writes run as a named STAFF login (`actor`) and need a policy receipt, exactly like the
# real API. The receipt is obtained before the transaction (see act()).
def _create_location(b: dict[str, Any], actor: str, decision: policy.Decision) -> dict[str, Any]:
    row = catalog.create_location(
        organization_ref=_text(b, "organization"), name=_text(b, "name"),
        time_zone=_text(b, "time_zone"), address=_text(b, "address", False),
        actor_public_id=actor, decision=decision)
    return {"public_id": row["public_id"]}


def _create_service(b: dict[str, Any], actor: str, decision: policy.Decision) -> dict[str, Any]:
    duration = b.get("duration_minutes")
    if not isinstance(duration, int) or isinstance(duration, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["duration_minutes"])
    row = catalog.create_service(
        organization_ref=_text(b, "organization"), name=_text(b, "name"),
        category=_text(b, "category"), duration_minutes=duration,
        mode=_text(b, "mode", False) or "in_person", description=_text(b, "description", False),
        actor_public_id=actor, decision=decision)
    return {"public_id": row["public_id"]}


def _create_slots(b: dict[str, Any], actor: str, decision: policy.Decision) -> dict[str, Any]:
    count = b.get("count")
    if not isinstance(count, int) or isinstance(count, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["count"])
    try:
        first = dt.datetime.fromisoformat(_text(b, "first_local"))
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["first_local"]) from None
    slots = scheduling.create_slots(
        service_ref=_text(b, "service"), location_ref=_text(b, "location"),
        first_local=first, count=count, provider_ref=_text(b, "provider", False),
        actor_public_id=actor, decision=decision)
    return {"created": len(slots)}


_CATALOG_ACTIONS: dict[str, Callable[[dict[str, Any], str, policy.Decision], dict[str, Any]]] = {
    "location": _create_location,
    "service": _create_service,
    "slots": _create_slots,
}

_TENANT_ACTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "set-password": _set_password,
    "self-registration": _self_registration,
    "person": _create_person,
    "link-person": _link_person,
    "care-start": _care_start,
    "care-end": _care_end,
    "care-contest": _care_contest,
    "proxy-propose": _proxy_propose,
    "proxy-approve": _proxy_approve,
    "proxy-revoke": _proxy_revoke,
    "organization": _create_org,
    "actor": _register_actor,
    "assign-role": _assign_role,
    "end-assignment": _end_assignment,
    "actor-status": _set_status,
    "decide": _decide,
}


def _in_tenant(body: dict[str, Any]) -> CellContext:
    return trust.context_for(_text(body, "tenant"))


def act(request: HttpRequest, action: str) -> JsonResponse:
    _guard()
    if request.method != "POST":
        raise Http404
    body = _body(request)
    if action == "tenant":
        tenant = trust.create_tenant(
            name=_text(body, "name"), type=_enum(trust.TenantType, body, "type"))
        return JsonResponse({"public_id": tenant.public_id})
    if action in _CATALOG_ACTIONS:
        return _catalog_action(body, _CATALOG_ACTIONS[action], action)
    handler = _TENANT_ACTIONS.get(action)
    if handler is None:
        raise Http404
    with tenant_transaction(_in_tenant(body)):
        return JsonResponse(handler(body))


def _catalog_action(
    body: dict[str, Any],
    handler: Callable[[dict[str, Any], str, policy.Decision], dict[str, Any]],
    action: str,
) -> JsonResponse:
    """Authorize `actor` for write:catalog FIRST (outside any transaction, so a denial is
    recorded), then do the write with the receipt."""
    ctx = _in_tenant(body)
    actor = _text(body, "actor")
    if action == "slots":                     # slots belong to a service: that decides the org
        with tenant_transaction(ctx):
            org = catalog.get_service(_text(body, "service"))["organization_ref"]
    else:
        org = _text(body, "organization")
    with bind_context(ctx):
        decision = policy.authorize(
            actor_public_id=actor, operation="write:catalog", purpose_code="OPERATIONS",
            organization_public_id=org)
    with tenant_transaction(ctx):
        return JsonResponse(handler(body, actor, decision))


def state(request: HttpRequest) -> JsonResponse:
    """Everything the page shows. Without ?tenant= it lists the tenants only."""
    _guard()
    public_id = request.GET.get("tenant", "")
    out: dict[str, Any] = {
        "cell": settings.ZOIKO_CELL_ID, "tenants": trust.list_tenants(),
        "operations": [], "purposes": [],
    }
    if public_id:
        with tenant_transaction(trust.context_for(public_id)):
            report = evidence.verify_chain()
            out.update(
                overview=trust.overview(),
                decisions=policy.recent_decisions(),
                events=evidence.recent_events(),
                chain={"ok": report.ok, "checked": report.checked,
                       "problem": report.problem, "bad_seq": report.first_bad_seq},
            )
    return JsonResponse(out)


def page(request: HttpRequest) -> HttpResponse:
    _guard()
    return HttpResponse(_PAGE.read_text(encoding="utf-8"), content_type="text/html")
