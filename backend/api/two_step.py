"""Two-step sign-in endpoints (DEV-380). The rules are in modules/trust/two_step.py.

  POST auth/login/second-step  {ticket, code}       the code after the password: a session
  GET  auth/two-step                                 on or off, recovery codes left, required?
  POST auth/two-step/set-up    {password}            a new secret to add to the app (shown once)
  POST auth/two-step/confirm   {code}                its first code: on, ten recovery codes
  POST auth/two-step/off       {password, code}      off (refused while the customer requires it)
  POST auth/two-step/recovery-codes {code}           ten new recovery codes
  POST auth/two-step/recheck   {code}                before a risky action: a fresh session
  GET/POST staff/two-step-rule {required}            the administrator requires it for all staff

A wrong password or code is a 422 naming the field (never a 401, so the app does not take it for
an expired session), committed first so the wrong-attempt counter is kept. Every answer that holds
a secret, a token or recovery codes is `Cache-Control: no-store`.
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.trust import api as trust

from .support import endpoint, json_body, require_recent_second_step
from .views import _authorize, _login_json, _no_store, _staff_only


def _wrong(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


@csrf_exempt
@require_POST
def login_second_step(request: HttpRequest) -> HttpResponse:
    """The ticket from the right password, and a code from the app (or a recovery code). Every
    failure is the same 401, like the password step."""
    body = json_body(request)
    result = trust.finish_two_step_sign_in(ticket=body.get("ticket"), code=body.get("code"))
    return _no_store(JsonResponse(_login_json(result)))


@endpoint("GET", during_set_up=True)
def two_step(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    with tenant_transaction():
        return JsonResponse({"two_step": trust.two_step_status(auth.actor_public_id)})


@endpoint("POST", during_set_up=True)
def two_step_set_up(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    body = json_body(request)
    with tenant_transaction():
        made = trust.start_two_step_set_up(auth.actor_public_id, password=body.get("password"))
    if not made.ok:
        raise _wrong("password")                 # after the commit: the counter is kept
    return _no_store(JsonResponse({"secret": made.secret, "link": made.link}))


@endpoint("POST", during_set_up=True)
def two_step_confirm(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    body = json_body(request)
    with tenant_transaction():
        done = trust.finish_two_step_set_up(auth.actor_public_id, code=body.get("code"))
    if not done.ok:
        raise _wrong("code")
    return _no_store(JsonResponse(
        {"token": done.token, "recovery_codes": list(done.recovery_codes)}))


@endpoint("POST")
def two_step_off(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    body = json_body(request)
    with tenant_transaction():
        done = trust.switch_off_two_step(
            auth.actor_public_id, password=body.get("password"), code=body.get("code"))
    if not done.ok:
        raise _wrong("password_or_code")
    return _no_store(JsonResponse({"token": done.token}))


@endpoint("POST")
def two_step_recovery_codes(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    body = json_body(request)
    with tenant_transaction():
        done = trust.new_recovery_codes(auth.actor_public_id, code=body.get("code"))
    if not done.ok:
        raise _wrong("code")
    return _no_store(JsonResponse({"recovery_codes": list(done.recovery_codes)}))


@endpoint("POST")
def two_step_recheck(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    body = json_body(request)
    with tenant_transaction():
        token = trust.recheck_two_step(auth.actor_public_id, code=body.get("code"))
    if token is None:
        raise _wrong("code")
    return _no_store(JsonResponse({"token": token}))


@endpoint("GET", "POST")
def staff_two_step_rule(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: whether this customer requires two-step sign-in for staff, and how many staff logins
    have not set it up. POST {"required": bool}: the clinic administrator (R13) only. Turning it
    ON needs their own two-step on; turning it OFF is a risky act and re-asks for a code."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:directory", organization_public_id=org)
    if request.method == "POST":
        if "R13" not in auth.roles:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        _authorize(auth, "write:staff", organization_public_id=org)
        wanted = json_body(request).get("required")
        if wanted is False:
            require_recent_second_step(auth)
        with tenant_transaction():
            trust.set_staff_two_step_requirement(
                wanted, by=auth.actor_public_id, by_has_two_step=auth.two_step_on)
    with tenant_transaction():
        mine = trust.two_step_status(auth.actor_public_id)
        without = trust.staff_without_two_step()
    return JsonResponse({        # this person is staff, so "required" is the customer's rule
        "required": mine["required"], "staff_without_two_step": without,
        "can_set": "R13" in auth.roles, "mine_on": mine["on"]})


@endpoint("POST")
def staff_member_reset_two_step(
    request: HttpRequest, auth: trust.Authenticated, member_id: str
) -> HttpResponse:
    """A clinic administrator resets a staff member's lost two-step sign-in (DEV-380)."""
    org = _staff_only(auth)
    if org is None or "R13" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    decision = _authorize(auth, "write:staff", organization_public_id=org)
    require_recent_second_step(auth)
    with tenant_transaction():
        done = trust.admin_reset_two_step(
            member_id, by=auth.actor_public_id, organization_public_id=str(org), decision=decision
        )
    return JsonResponse({"member": done})
