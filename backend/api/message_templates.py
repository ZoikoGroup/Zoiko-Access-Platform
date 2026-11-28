"""Message templates in the platform console (DEV-386, GHE-16 WF-15). Rules: foundation/
template_policy.py and foundation/message_templates.py.

  GET  platform/templates                                  every template, version, sign-offs,
                                                           held messages and last approved wording
  POST platform/templates/<channel>/<key>/approve {as}     as = clinical (a safety officer, R12)
                                                           or privacy (Zoiko support, R11)

The same person can never give both sign-offs. The second one approves the version and sends what
was held for it. Each sign-off re-asks for the two-step code when the last one is stale.
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation import template_policy
from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence import api as evidence
from modules.trust import api as trust

from .support import endpoint, json_body, require_recent_second_step
from .views import _authorize, _platform_only

ROLE_FOR = {template_policy.CLINICAL: "R12", template_policy.PRIVACY: "R11"}


@endpoint("GET")
def platform_templates(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        rows = template_policy.listing(trust.actor_labels())
    return JsonResponse({
        "templates": rows, "mode": template_policy.mode(),
        "may_sign_as": [k for k, role in ROLE_FOR.items() if role in auth.roles],
    })


@endpoint("POST")
def platform_template_approve(
    request: HttpRequest, auth: trust.Authenticated, channel: str, key: str,
) -> HttpResponse:
    org = _platform_only(auth)
    _authorize(auth, "attest:platform", organization_public_id=org)
    as_role = json_body(request).get("as")
    if as_role not in ROLE_FOR:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["as"])
    if ROLE_FOR[str(as_role)] not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    require_recent_second_step(auth)
    with tenant_transaction():
        done = template_policy.approve(channel=channel, key=key, as_role=str(as_role),
                                       actor_ref=auth.actor_public_id)
        evidence.record(actor_ref=auth.actor_public_id, action="template.sign_off",
                        object_type="message_template", object_ref=key[:64],
                        outcome=evidence.AuditOutcome.ALLOWED, reason_code=str(as_role),
                        details={"channel": channel, "version": done["version"],
                                 "approved": done["approved"], "released": done["released"]})
    return JsonResponse({"template": done})
