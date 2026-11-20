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
        merged = trust.merge_of([c["public_id"] for c in cases])
    for case in cases:
        for step in case["history"]:
            step["by"] = _who(step.pop("actor_ref"), labels)
        first = case.pop("first_reviewer_ref")
        case["first_reviewer"] = labels.get(first, "") if first else ""
        case["you_agreed_first"] = bool(first) and first == auth.actor_public_id
        case["merge"] = merged.get(case["public_id"])
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


@endpoint("POST")
def staff_match_merge(
    request: HttpRequest, auth: trust.Authenticated, case_id: str
) -> HttpResponse:
    """Merge the two records of a confirmed link into one. A separate step from confirming. The
    reviewer may name which record survives (`primary`); otherwise the older one does."""
    org = _org(auth)
    _authorize(auth, "write:person", organization_public_id=org)
    body = json_body(request)
    with tenant_transaction():
        done = trust.merge_people(
            case_ref=case_id, actor_public_id=auth.actor_public_id, organization_ref=org,
            primary_ref=text(body, "primary", required=False))
    return JsonResponse({"case": {"id": done["case"]["public_id"], "state": done["case"]["state"]},
                         "merge": done["merge"]})


@endpoint("POST")
def staff_match_unmerge(
    request: HttpRequest, auth: trust.Authenticated, case_id: str
) -> HttpResponse:
    """Undo a merge: what it moved goes back, and the case returns to review."""
    org = _org(auth)
    _authorize(auth, "write:person", organization_public_id=org)
    with tenant_transaction():
        done = trust.unmerge_people(
            case_ref=case_id, actor_public_id=auth.actor_public_id, organization_ref=org)
    return JsonResponse({"case": {"id": done["case"]["public_id"], "state": done["case"]["state"]},
                         "merge": done["merge"]})


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



# --------------------------------------------------------------------------- a death (DEV-333)
def _death_json(info: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    by = info.pop("recorded_by")
    return {**info, "recorded_by_name": labels.get(by, "") if by else "",
            "recorded_by_me": False, "recorded_by": by}


@endpoint("GET", "POST")
def person_death(request: HttpRequest, auth: trust.Authenticated, person_id: str) -> HttpResponse:
    """GET: whether this patient is recorded as having died. POST {died_on, source}: a clinic
    ADMINISTRATOR records it; the patient's future bookings are cancelled with the reason "the
    patient has died", which also stops their reminders. Withdrawing needs a second person."""
    from modules.scheduling import api as scheduling

    org = _org(auth)
    if request.method == "POST":
        if "R13" not in auth.roles:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        decision = _authorize(auth, "write:person", organization_public_id=org,
                              subject_ref=person_id)
        body = json_body(request)
        with tenant_transaction():
            if not trust.person_at_site(person_id, org):
                raise ZoikoError(ErrorCode.NOT_FOUND)
            info = trust.record_death(
                person_ref=person_id, died_on=body.get("died_on"), source=body.get("source"),
                by=auth.actor_public_id, decision=decision, organization_ref=org)
            open_ones = scheduling.future_bookings_of(person_id)
        cancelled = 0
        if open_ones:
            booking = _authorize(auth, "write:booking", organization_public_id=org,
                                 subject_ref=person_id)
            for ref in open_ones:
                try:
                    with tenant_transaction():
                        scheduling.cancel(booking_ref=ref, actor_public_id=auth.actor_public_id,
                                          decision=booking, reason="patient_died")
                    cancelled += 1
                except ZoikoError:
                    continue  # e.g. attendance already recorded: that history stays
    else:
        _authorize(auth, "read:person", organization_public_id=org, subject_ref=person_id)
        cancelled = 0
        with tenant_transaction():
            if not trust.person_at_site(person_id, org):
                raise ZoikoError(ErrorCode.NOT_FOUND)
            info = trust.death_of(person_id)
    with tenant_transaction():
        labels = trust.actor_labels()
    out = _death_json(info, labels)
    out["recorded_by_me"] = out["recorded_by"] == auth.actor_public_id
    out["can_record"] = "R13" in auth.roles
    return JsonResponse({"death": out, "bookings_cancelled": cancelled},
                        status=201 if request.method == "POST" else 200)


@endpoint("POST")
def person_death_withdraw(
    request: HttpRequest, auth: trust.Authenticated, person_id: str
) -> HttpResponse:
    """A second administrator withdraws a death recorded in error {reason}. Cancelled bookings
    are not restored: the clinic books again with the patient."""
    org = _org(auth)
    if "R13" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    decision = _authorize(auth, "write:person", organization_public_id=org, subject_ref=person_id)
    with tenant_transaction():
        if not trust.person_at_site(person_id, org):
            raise ZoikoError(ErrorCode.NOT_FOUND)
        info = trust.withdraw_death(person_ref=person_id, reason=json_body(request).get("reason"),
                                    by=auth.actor_public_id, decision=decision,
                                    organization_ref=org)
        labels = trust.actor_labels()
    return JsonResponse({"death": _death_json(info, labels)})
