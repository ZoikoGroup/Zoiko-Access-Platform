"""Outgoing-address approvals in the platform console (DEV-384); rules in integration/egress.py.

  GET  platform/egress                      every permit (waiting first), and the gating mode
  POST platform/egress                      {customer, host, purpose}: Zoiko staff propose one
  POST platform/egress/<id>/<approve|refuse|revoke>   {reason} (refuse and revoke need one)

Zoiko's own staff only: support (R11) and safety (R12) may decide; nobody approves their own
proposal. Clinics ask by registering a webhook address (staff/integrations/webhooks).
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.integration import api as integration
from modules.trust import api as trust

from .support import endpoint, json_body, require_recent_second_step
from .views import _authorize, _platform_only


def _customers() -> dict[str, str]:
    return {t["public_id"]: t["name"] for t in trust.list_tenants() if t["type"] != "platform"}


@endpoint("GET", "POST")
def platform_egress(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _platform_only(auth)
    if request.method == "POST":
        _authorize(auth, "write:platform", organization_public_id=org)
        body = json_body(request)
        customer = str(body.get("customer", ""))
        if customer not in _customers():
            raise ZoikoError(ErrorCode.NOT_FOUND)
        host = str(body.get("host", "")).strip().lower().rstrip(".")
        state = integration.ask_egress(customer, host, str(body.get("purpose", "")),
                                       proposed_by=auth.actor_public_id)
        return JsonResponse({"state": state}, status=201)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        rows = integration.egress_permits(trust.actor_labels(), _customers())
    return JsonResponse({
        "permits": rows, "mode": integration.egress_mode(),
        "customers": [{"id": k, "name": v} for k, v in sorted(_customers().items(),
                                                             key=lambda kv: kv[1])],
        "can_decide": bool({"R11", "R12"} & set(auth.roles)),
    })


@endpoint("POST")
def platform_egress_step(
    request: HttpRequest, auth: trust.Authenticated, permit_id: str, step: str,
) -> HttpResponse:
    org = _platform_only(auth)
    _authorize(auth, "write:platform", organization_public_id=org)
    if step == "approve":
        require_recent_second_step(auth)          # opening a path out of the cell (DEV-380)
    body = json_body(request)
    with tenant_transaction():
        done = integration.decide_egress(permit_id, step=step, by=auth.actor_public_id,
                                         reason=body.get("reason", ""))
    return JsonResponse({"permit": done})
