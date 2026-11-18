"""A patient's own control over who can see their information (modules/trust/access.py,
modules/trust/age_review.py, modules/consent).

  GET  access                                 who may act for me, my sharing choices, my age review
  POST access/consent   {purpose, grant, days?}      say yes (for good, or for N days) or no
  POST access/delegates/<id>/narrow {scopes?, valid_until?}   give a guardian LESS access
  POST access/delegates/<id>/end                              end a guardian's access
  POST access/delegates/<id>/keep                             after coming of age: keep their access

Only the person themselves, through their own login, can use these for their own record. A guardian
cannot widen their own access here; that is a clinic's decision after proof (family requests).
"""
from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.consent import api as consent
from modules.trust import api as trust

from .support import endpoint, json_body
from .views import _authorize, _catalog_org

ACTIONS = ("narrow", "end", "keep")


def _me(auth: trust.Authenticated) -> tuple[str, str | None]:
    """The patient record this login IS (its own), and the organization to check against."""
    org = _catalog_org(auth)
    with tenant_transaction():
        person = trust.own_person_of(auth.actor_public_id)
    return person, org


def _delegate_json(grant: Any) -> dict[str, Any]:
    return {"id": grant.public_id, "status": grant.status, "scopes": list(grant.rights or []),
            "valid_until": grant.valid_until.isoformat() if grant.valid_until else None}


@endpoint("GET")
def who_can_see_me(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    person, org = _me(auth)
    if not person or org is None:
        return JsonResponse(
            {"person": None, "delegates": [], "consents": [], "age_of_majority": 18})
    _authorize(auth, "read:person", organization_public_id=org, subject_ref=person)
    with tenant_transaction():
        overview = trust.access_overview(person)
        consents = consent.list_for_person(person)
    return JsonResponse({"person": person, **overview, "consents": consents})


@endpoint("POST")
def my_consent(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    person, org = _me(auth)
    if not person or org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    body = json_body(request)
    purpose = body.get("purpose")
    if not isinstance(purpose, str):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["purpose"])
    grant = body.get("grant")
    if not isinstance(grant, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["grant"])
    resource = consent.purpose_resource(purpose)          # 422 for an unknown purpose
    _authorize(auth, f"write:{resource}", organization_public_id=org, subject_ref=person)
    with tenant_transaction():
        done = consent.set_consent(
            person_ref=person, purpose=purpose, grant=grant, actor_ref=auth.actor_public_id,
            days=body.get("days"))
    return JsonResponse({"consent": done})


@endpoint("POST")
def my_delegate_action(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str, action: str
) -> HttpResponse:
    if action not in ACTIONS:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    person, org = _me(auth)
    if not person or org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "write:person", organization_public_id=org, subject_ref=person)
    body = json_body(request)
    with tenant_transaction():
        if trust.proxy_person_of(grant_id) != person:       # someone else's guardian: not found
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if action == "narrow":
            scopes = body.get("scopes")
            if scopes is not None and not isinstance(scopes, list):
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["scopes"])
            valid_until = body.get("valid_until") or ""
            if not isinstance(valid_until, str):
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["valid_until"])
            done = trust.narrow_access(
                grant_ref=grant_id, actor_public_id=auth.actor_public_id, scopes=scopes,
                valid_until=valid_until)
        elif action == "keep":
            done = trust.keep_access(grant_ref=grant_id, actor_public_id=auth.actor_public_id)
        else:
            done = trust.end_access(grant_ref=grant_id, actor_public_id=auth.actor_public_id)
    return JsonResponse({"delegate": _delegate_json(done)})
