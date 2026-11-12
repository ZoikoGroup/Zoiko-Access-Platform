"""Endpoints for identity matching and identity checks (GHE-06).

Clinic staff decide possible duplicates and record identity checks for patients of their own clinic;
a patient may see only whether they are verified. The patient never sees that a possible duplicate
is under review (the specification: never show a match result to the patient while it is
unresolved). Every action is policy-checked in the person's own organization.
"""
from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.trust import api as trust

from .support import endpoint, is_allowed, json_body, text
from .views import _authorize, _catalog_org, _is_staff, _staff_only


def _org(auth: trust.Authenticated) -> str:
    org = _staff_only(auth)
    if not org:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return org


def _who(ref: str, labels: dict[str, str]) -> str:
    return "The system" if ref == "system" else labels.get(ref, "Someone")


# --------------------------------------------------------------------------- possible duplicates


@endpoint("GET")
def staff_matches(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Possible duplicate patients of this clinic, held for a person to decide. Masked: how the
    records compared, initials, birth year and the last two phone digits, never full details."""
    org = _org(auth)
    _authorize(auth, "read:person", organization_public_id=org)
    can_decide = is_allowed(auth, "write:person", organization_public_id=org)
    with tenant_transaction():
        cases = trust.list_cases(org)
        labels = trust.actor_labels()
    for case in cases:
        for step in case["history"]:
            step["by"] = _who(step.pop("actor_ref"), labels)
        first = case.pop("first_reviewer_ref")
        case["first_reviewer"] = labels.get(first, "") if first else ""
        case["you_agreed_first"] = bool(first) and first == auth.actor_public_id
    return JsonResponse({"cases": cases, "can_decide": can_decide})


@endpoint("POST")
def staff_match_decide(
    request: HttpRequest, auth: trust.Authenticated, case_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "write:person", organization_public_id=org)
    body = json_body(request)
    with tenant_transaction():
        done = trust.decide_match(
            case_ref=case_id, action=text(body, "action"), actor_public_id=auth.actor_public_id,
            organization_ref=org, note=text(body, "note", required=False))
    return JsonResponse({"case": {"id": done["public_id"], "state": done["state"]}})


# --------------------------------------------------------------------------- identity checks


def _identity_json(info: dict[str, Any], orgs: dict[str, str], staff: bool) -> dict[str, Any]:
    out = {
        "status": info["status"], "checked_on": info["checked_on"],
        "valid_until": info["valid_until"], "clinic": orgs.get(info["clinic_ref"], ""),
        "why_not": info["why_not"],
    }
    if staff:
        out["under_review"] = info["under_review"]   # a patient is never told (GHE-06 W03)
    return out


@endpoint("GET")
def person_identity(
    request: HttpRequest, auth: trust.Authenticated, person_id: str
) -> HttpResponse:
    """Whether a patient is verified. Staff of a clinic the patient is connected to also see the
    history of checks and whether a possible duplicate is under review."""
    org = _catalog_org(auth)
    _authorize(auth, "read:person", organization_public_id=org, subject_ref=person_id)
    staff = _is_staff(auth)
    with tenant_transaction():
        if staff and not (org and trust.person_at_site(person_id, org)):
            raise ZoikoError(ErrorCode.NOT_FOUND)
        info = trust.identity_status_of(person_id)
        orgs = trust.organization_names()
        labels = trust.actor_labels()
        past = trust.identity_history(person_id) if staff else []
    body: dict[str, Any] = {"identity": _identity_json(info, orgs, staff)}
    if staff:
        body["history"] = [
            {"id": r["id"], "evidence_type": r["evidence_type"], "outcome": r["outcome"],
             "clinic": orgs.get(r["organization_ref"], ""),
             "by": _who(r["checked_by_ref"], labels), "checked_at": r["checked_at"],
             "valid_until": r["valid_until"], "withdrawn": r["withdrawn"],
             "withdraw_reason": r["withdraw_reason"]} for r in past]
    return JsonResponse(body)


@endpoint("POST")
def person_identity_check(
    request: HttpRequest, auth: trust.Authenticated, person_id: str
) -> HttpResponse:
    """Record that clinic staff checked this patient's identity against an official document."""
    org = _org(auth)
    _authorize(auth, "write:person", organization_public_id=org, subject_ref=person_id)
    body = json_body(request)
    days = body.get("valid_days")
    with tenant_transaction():
        if not trust.person_at_site(person_id, org):
            raise ZoikoError(ErrorCode.NOT_FOUND)
        info = trust.record_identity_check(
            person_public_id=person_id, evidence_type=text(body, "evidence_type"),
            outcome=text(body, "outcome"), organization_ref=org,
            actor_public_id=auth.actor_public_id, valid_days=days)
        orgs = trust.organization_names()
    return JsonResponse({"identity": _identity_json(info, orgs, True)}, status=201)


@endpoint("POST")
def person_identity_withdraw(
    request: HttpRequest, auth: trust.Authenticated, person_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "write:person", organization_public_id=org, subject_ref=person_id)
    reason = text(json_body(request), "reason")
    with tenant_transaction():
        if not trust.person_at_site(person_id, org):
            raise ZoikoError(ErrorCode.NOT_FOUND)
        info = trust.withdraw_identity_check(
            person_public_id=person_id, reason=reason, actor_public_id=auth.actor_public_id)
        orgs = trust.organization_names()
    return JsonResponse({"identity": _identity_json(info, orgs, True)})
