"""The 30-day error budget and its release hold (DEV-388). Rules: foundation/error_budget.py and
modules/readiness/services.py (`_budget_state`, `override_budget`).

  GET  platform/error-budget                          both budgets, burn rates, alerts + runbooks
  POST platform/readiness/<id>/budget-override        {reason, days}: a safety officer lets ONE
                                                       release go on while the budget is used up
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation import error_budget
from foundation.cell_context import tenant_transaction
from modules.readiness import api as readiness
from modules.trust import api as trust

from .support import endpoint, json_body, require_recent_second_step
from .views import _authorize, _platform_only, _release_json


@endpoint("GET")
def platform_error_budget(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    return JsonResponse({**error_budget.summary(), "runbooks": sorted(set(
        error_budget.RUNBOOKS.values()) | set(OTHER_RUNBOOKS))})


# Runbooks for alerts shown elsewhere in the console (texts, mail, deliveries, rate limits).
OTHER_RUNBOOKS = (
    "docs/runbooks/03-database-unavailable.md",
    "docs/runbooks/04-email-not-going-out.md",
    "docs/runbooks/05-texts-circuit-open.md",
    "docs/runbooks/06-partner-deliveries-stuck.md",
    "docs/runbooks/07-request-flood.md",
)


@endpoint("POST")
def platform_budget_override(
    request: HttpRequest, auth: trust.Authenticated, release_id: str,
) -> HttpResponse:
    org = _platform_only(auth)
    decision = _authorize(auth, "attest:platform", organization_public_id=org)
    require_recent_second_step(auth)                 # releasing past a used-up budget (DEV-380)
    body = json_body(request)
    with tenant_transaction():
        done = readiness.override_budget(
            release_ref=release_id, reason=str(body.get("reason", "")), days=body.get("days"),
            actor_public_id=auth.actor_public_id, decision=decision, organization_ref=org)
        labels = trust.actor_labels()
    return JsonResponse({"release": _release_json(done, labels)})
