"""Holding a result while who it belongs to is in doubt (DEV-385, GHE-06 I-05: ambiguous match ->
DENY / HOLD). Two causes:

- the patient is in an OPEN duplicate-record case (staff/matches): the record a result went to may
  be the wrong one of the pair, so it is held until the case is decided;
- the patient (or someone acting for them) DISPUTES the result: "not mine", "wrong values",
  "I did not have this test" (a reason code; no free text).

Held means: the patient sees that it is held and why, never its values or its trend; and a doctor
cannot mark it reviewed. The doctor still sees the result, with the reason it is held.

  POST exchange/mine/<id>/dispute       {reason}           the patient disputes
  POST exchange/requests/<id>/dispute   {outcome, note}    the treating doctor decides:
                                                            confirmed | not_theirs
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.evidence import api as evidence
from modules.exchange import api as exchange
from modules.trust import api as trust

from .support import endpoint, json_body

IDENTITY_CHECK = "identity_check"


def identity_held(person_refs: list[str]) -> set[str]:
    """Which of these patients are in a duplicate-record case still waiting for a decision.
    Runs in the customer's own space (where the cases are)."""
    with tenant_transaction():
        return trust.open_case_people(sorted(set(person_refs)))


def refuse_if_identity_held(person_ref: str) -> None:
    if identity_held([person_ref]):
        raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["identity_check_open"])


@endpoint("POST")
def exchange_mine_dispute(
    request: HttpRequest, auth: trust.Authenticated, request_id: str,
) -> HttpResponse:
    from .views import _EXCHANGE_READ, _authorize, _my_customer, _platform_context

    mine = _my_customer(auth)
    with tenant_transaction():
        reachable = set(trust.reachable_persons(auth.actor_public_id))
    with tenant_transaction(_platform_context()):
        person = exchange.person_of(request_id, mine)
        kind = exchange.sent_kind_of(request_id, mine)
    if person not in reachable:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    _authorize(auth, _EXCHANGE_READ[kind], subject_ref=person)
    reason = json_body(request).get("reason")
    with tenant_transaction(_platform_context()):
        done = exchange.raise_dispute(
            request_ref=request_id, tenant_ref=mine, person_refs=[person],
            actor_ref=auth.actor_public_id, reason=reason)
    with tenant_transaction():
        evidence.record(actor_ref=auth.actor_public_id, action="exchange.result_disputed",
                        object_type="exchange_request", object_ref=request_id,
                        outcome=evidence.AuditOutcome.ALLOWED, reason_code=str(reason))
    return JsonResponse({"dispute": done}, status=201)


@endpoint("POST")
def exchange_request_dispute(
    request: HttpRequest, auth: trust.Authenticated, request_id: str,
) -> HttpResponse:
    """A treating doctor decides a patient's dispute (the same right as reviewing the result)."""
    from .views import _add_names, _authorize, _clinic_org, _my_customer, _platform_context

    org = _clinic_org(auth)
    if "R03" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        person = exchange.person_of(request_id, mine)
    _authorize(auth, "attest:referral", organization_public_id=org, subject_ref=person)
    body = json_body(request)
    with tenant_transaction(_platform_context()):
        done = exchange.decide_dispute(
            request_ref=request_id, tenant_ref=mine, actor_ref=auth.actor_public_id,
            outcome=body.get("outcome"), note=body.get("note", ""))
        incident_id: str | None = None
        if done.get("dispute") == "not_theirs":
            inc = trust.record_system_incident(
                title="Wrong-patient lab result reported (dispute)",
                details=(
                    f"Exchange request {request_id} in tenant {mine} was confirmed not_theirs "
                    "by the treating clinician. Review with the partner laboratory."
                ),
                safety_impact=True,
            )
            incident_id = inc["public_id"]
    with tenant_transaction():
        evidence.record(actor_ref=auth.actor_public_id,
                        action=f"exchange.dispute_{done['dispute']}",
                        object_type="exchange_request", object_ref=request_id,
                        outcome=evidence.AuditOutcome.ALLOWED)
    out = [done]
    _add_names(auth, out, "patient")
    payload: dict[str, object] = {"request": out[0]}
    if incident_id is not None:
        payload["incident_id"] = incident_id
    return JsonResponse(payload)
