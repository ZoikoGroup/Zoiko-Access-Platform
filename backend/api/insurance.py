"""Staff "Insurance status": what a clinic can truthfully say about its patients' insurance today,
and who to ask before a visit (DEV-368, ported from the maruthi branch's Billing & Claims overview).

A read-only summary of what already exists: the insurance each patient recorded, and the visits
booked at this clinic. Invoices and claims are their own pages (DEV-139, DEV-308).

Privacy: a patient's insurance is visible to staff only if the patient chose to share it (DEV-096;
the default is not shared). For a patient who has not shared, this page says exactly that and
nothing else: not even whether they have insurance. Member numbers are never returned (only the
last four characters the coverage module already exposes). Only the clinic's own patients are
listed (DEV-107).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import utc_now
from modules.coverage import api as coverage
from modules.scheduling import api as scheduling
from modules.trust import api as trust

from .support import endpoint
from .views import _add_names, _authorize, _staff_only

ENDING_SOON_DAYS = 30

# What each patient's row says. "not_shared" is deliberately the only thing known about them then.
COVERED, ENDING_SOON = "covered", "ending_soon"
NONE_IN_FORCE, NOT_SHARED = "none_in_force", "not_shared"


def _state(rows: list[dict[str, Any]], today: dt.date) -> tuple[str, dict[str, Any] | None]:
    """The patient's cover today, from rows they chose to share, and the row it comes from."""
    in_force = [r for r in rows if r["in_force"]]
    if not in_force:
        return NONE_IN_FORCE, None
    # Prefer the one that lasts longest (no end date counts as the longest).
    best = max(in_force, key=lambda r: r["valid_until"] or "9999-12-31")
    if best["valid_until"]:
        left = (dt.date.fromisoformat(best["valid_until"]) - today).days
        if left <= ENDING_SOON_DAYS:
            return ENDING_SOON, best
    return COVERED, best


@endpoint("GET")
def staff_insurance_status(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:coverage", organization_public_id=org)
    now = utc_now()
    today = now.date()
    with tenant_transaction():
        # Only this clinic's own patients: the same rule as the per-patient insurance view.
        everyone = [p["id"] for p in trust.patient_registry()["patients"]]
        mine = trust.people_at_site(everyone, org)
        refs = [r for r in everyone if r in mine]
        shared = coverage.shared_with_staff(refs)
        rows = coverage.list_coverage([r for r in refs if shared[r]], include_ended=True)
        upcoming = scheduling.patients_with_upcoming_visits(org)
    by_person: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_person.setdefault(row["person_ref"], []).append(row)

    people: list[dict[str, Any]] = []
    for ref in refs:
        cover: dict[str, Any] | None
        if shared[ref]:
            state, cover = _state(by_person.get(ref, []), today)
        else:
            state, cover = NOT_SHARED, None
        people.append({
            "id": ref, "state": state, "upcoming_visit": ref in upcoming,
            "insurer": cover["insurer_name"] if cover else "",
            "plan": cover["plan_name"] if cover else "",
            "valid_until": cover["valid_until"] if cover else None,
            "member_last4": cover["member_last4"] if cover else "",
        })
    _add_names(auth, people, "id")

    def count(state: str) -> int:
        return sum(1 for p in people if p["state"] == state)

    # Who the front desk should ask before the visit: nothing in force, about to end, or the patient
    # has not shared (so the clinic cannot tell). Only patients with a visit still to come.
    attention = [p for p in people if p["upcoming_visit"] and p["state"] != COVERED]
    return JsonResponse({
        "counts": {
            "patients": len(people), "covered": count(COVERED), "ending_soon": count(ENDING_SOON),
            "none_in_force": count(NONE_IN_FORCE), "not_shared": count(NOT_SHARED),
            "needs_attention": len(attention),
        },
        "ending_soon_days": ENDING_SOON_DAYS,
        "patients": people,
        "attention": attention,
    })
