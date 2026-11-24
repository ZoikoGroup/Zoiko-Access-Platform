"""A patient's own control over who can see their information (modules/trust/access.py,
modules/trust/age_review.py, modules/consent).

  GET  access                                 who may act for me, my sharing choices, my age review
  POST access/consent   {purpose, grant, days?}      say yes (for good, or for N days) or no
  POST access/delegates/<id>/narrow {scopes?, valid_until?}   give a guardian LESS access
  POST access/delegates/<id>/end                              end a guardian's access
  POST access/delegates/<id>/keep                             after coming of age: keep their access
  GET  disclosures                            everything sent about me to another organization

Only the person themselves, through their own login, can use these for their own record. A guardian
cannot widen their own access here; that is a clinic's decision after proof (family requests).
"""
from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.catalog import api as catalog
from modules.claim import api as claim
from modules.consent import api as consent
from modules.exchange import api as exchange
from modules.referral import api as referral
from modules.trust import api as trust

from .support import endpoint, is_allowed, json_body
from .views import _authorize, _catalog_org, _my_customer, _platform_context

DISCLOSURE_LIMIT = 200
_EXCHANGE_LABELS = {
    "lab_order": "A lab order",
    "pharmacy_request": "A pharmacy request",
    "homecare_referral": "A home-care referral",
}
_EXCHANGE_READ = {
    "lab_order": "read:diagnostic",
    "pharmacy_request": "read:medication",
    "homecare_referral": "read:referral",
}

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


def _disclosure_rows(auth: trust.Authenticated) -> list[dict[str, Any]]:
    """Every time information about a person this login may read was sent to ANOTHER organization,
    as one list: a referral to another service, a request to a partner, a claim to a payer. Built
    from those records, so it can never disagree with them. Newest first."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    if not reachable:
        return []
    rows: list[dict[str, Any]] = []
    allowed: dict[tuple[str, str], bool] = {}

    def may(operation: str, person: str) -> bool:
        key = (operation, person)
        if key not in allowed:
            allowed[key] = is_allowed(auth, operation, subject_ref=person)
        return allowed[key]

    persons = [p for p in reachable if may("read:referral", p)]
    if persons:
        with tenant_transaction():
            orgs = trust.organization_names()
            services: dict[str, Any] = {}
            for r in referral.list_referrals(person_refs=persons):
                service = services.setdefault(
                    r["service_ref"], catalog.get_service(r["service_ref"]))
                rows.append({
                    "id": r["public_id"], "kind": "referral", "person": r["person_ref"],
                    "at": r["created_at"], "to": orgs.get(r["destination_organization_ref"], ""),
                    "what": f"A referral for {service['name']}", "shared": True,
                    "state": r["state"]})
    asked: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    try:
        mine = _my_customer(auth)
        with tenant_transaction(_platform_context()):
            asked = exchange.list_for_patients(tenant_ref=mine, person_refs=reachable)
            claims = claim.list_for_patients(tenant_ref=mine, person_refs=reachable)
    except ZoikoError as exc:
        if exc.code != ErrorCode.SERVICE_UNAVAILABLE:
            raise
        # No platform space in this deployment: no partner request or claim can exist yet.
    for r in asked:
        if may(_EXCHANGE_READ[r["kind"]], r["person"]):
            rows.append({
                "id": r["id"], "kind": "request", "person": r["person"], "at": r["created_at"],
                "to": r["partner"], "what": f"{_EXCHANGE_LABELS[r['kind']]}: {r['label']}",
                # The partner holds the patient's name and birth date only while they agree.
                "shared": r["consent"] == "granted", "state": r["state"]})
    for c in claims:
        if may("read:coverage", c["person"]):
            rows.append({
                "id": c["id"], "kind": "claim", "person": c["person"], "at": c["created_at"],
                "to": c["payer"], "what": f"A claim for {c['label']}",
                # The payer sees the details only after the patient agrees.
                "shared": c["consent"] == "granted", "state": c["state"]})
    rows.sort(key=lambda r: str(r["at"]), reverse=True)
    # The patient sees who received what, never an internal id of a person.
    return [{k: v for k, v in r.items() if k != "person"} for r in rows[:DISCLOSURE_LIMIT]]


@endpoint("GET")
def my_disclosures(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The disclosure log: who my information was sent to, what for, and whether they hold the
    details now. Only for the people this login may read; names of other organizations only."""
    return JsonResponse({"entries": _disclosure_rows(auth)})
