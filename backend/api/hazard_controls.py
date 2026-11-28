"""Risk controls of the hazard register and the hazard -> control -> test trace (DEV-382).
The rules are in modules/safety/controls.py.

  GET  platform/hazards/<hazard>/controls         the controls of one hazard
  POST platform/hazards/<hazard>/controls         {kind, description, implementation, test_ref}
  POST platform/hazard-controls/<control>/verify   {note}: a DIFFERENT safety officer
  POST platform/hazard-controls/<control>/withdraw {reason}
  GET  platform/hazard-trace                       every hazard, its controls, tests and gaps

Zoiko's own staff only; support (R11) reads, safety officers (R12) decide.
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.safety import api as safety
from modules.trust import api as trust

from .support import endpoint, json_body
from .views import _authorize, _platform_only, _safety_officer


def _reader(auth: trust.Authenticated) -> None:
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)


@endpoint("GET", "POST")
def platform_hazard_controls(
    request: HttpRequest, auth: trust.Authenticated, hazard_id: str,
) -> HttpResponse:
    if request.method == "POST":
        _safety_officer(auth)
        body = json_body(request)
        with tenant_transaction():
            made = safety.propose_control(
                hazard_id, actor_ref=auth.actor_public_id, kind=body.get("kind"),
                description=body.get("description"), implementation=body.get("implementation"),
                test_ref=body.get("test_ref"))
        return JsonResponse({"control": made}, status=201)
    _reader(auth)
    with tenant_transaction():
        rows = safety.controls_of(hazard_id, trust.actor_labels())
    return JsonResponse({"controls": rows, "can_decide": "R12" in auth.roles})


@endpoint("POST")
def platform_hazard_control_step(
    request: HttpRequest, auth: trust.Authenticated, control_id: str, step: str,
) -> HttpResponse:
    if step not in ("verify", "withdraw"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    _safety_officer(auth)
    body = json_body(request)
    with tenant_transaction():
        if step == "verify":
            done = safety.verify_control(
                control_id, actor_ref=auth.actor_public_id, note=body.get("note"))
        else:
            done = safety.withdraw_control(
                control_id, actor_ref=auth.actor_public_id, reason=body.get("reason"))
    return JsonResponse({"control": done})


@endpoint("GET")
def platform_hazard_trace(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    _reader(auth)
    with tenant_transaction():
        safety.ensure_seed(auth.actor_public_id)
        data = safety.trace(trust.actor_labels())
    return JsonResponse({**data, "can_decide": "R12" in auth.roles})
