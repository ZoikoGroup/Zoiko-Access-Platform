"""Sign in with an outside identity provider: the two public endpoints (modules/trust/sso.py).

  POST auth/sso/start    {provider, organization, redirect_uri}      -> {url}
  POST auth/sso/finish   {provider, code, state, redirect_uri}       -> the same answer as a login

Both are open (nobody is signed in yet). Every failure is the same 401 as a wrong password.
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from modules.trust import api as trust

from .support import json_body, text


@csrf_exempt
@require_POST
def sso_start(request: HttpRequest) -> HttpResponse:
    body = json_body(request)
    url = trust.sso_start(
        provider_key=text(body, "provider"), tenant_name=text(body, "organization"),
        redirect_uri=text(body, "redirect_uri"))
    response = JsonResponse({"url": url})
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
def sso_finish(request: HttpRequest) -> HttpResponse:
    from .views import _login_json  # the one shape every sign-in answers with

    body = json_body(request)
    result = trust.sso_finish(
        provider_key=text(body, "provider"), code=text(body, "code"), state=text(body, "state"),
        redirect_uri=text(body, "redirect_uri"))
    response = JsonResponse(_login_json(result))
    response["Cache-Control"] = "no-store"
    return response
