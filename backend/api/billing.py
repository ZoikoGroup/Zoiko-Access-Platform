"""Staff "Billing & Claims": what a clinic can truthfully say about its patients' insurance today.

This is a read-only summary. There are no prices, invoices, payments or claims yet (they wait on
DEV-011 and DEV-012). It only reads what already exists: the insurance each patient recorded, and
the visits booked at this clinic.

Privacy: a patient's insurance is visible to staff only if the patient chose to share it (the
default is not shared). For a patient who has not shared, this page says exactly that and nothing
else: not even whether they have insurance. Member numbers are never returned (only the last four
characters that the coverage module already exposes).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.coverage import api as coverage
from modules.scheduling import api as scheduling
from modules.trust import api as trust

from .support import endpoint
from .views import _add_names, _authorize, _staff_only

ENDING_SOON_DAYS = 30
UPCOMING_STATUSES = ("requested", "confirmed")

# What each patient's row says. "not_shared" is deliberately the only thing we know about them then.
COVERED, ENDING_SOON = "covered", "ending_soon"
NONE_IN_FORCE, NOT_SHARED = "none_in_force", "not_shared"


def _upcoming_patients(org: str, now: dt.datetime) -> set[str]:
    """Patients with a visit at this clinic that is still to come and has not been cancelled."""
    soon = set()
    for b in scheduling.list_bookings(organization_ref=org, limit=200):
        starts = dt.datetime.fromisoformat(b["slot"]["starts_at"])
        if b["status"] in UPCOMING_STATUSES and starts >= now:
            soon.add(b["person_ref"])
    return soon


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
def staff_billing(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    _authorize(auth, "read:coverage", organization_public_id=org)
    now = utc_now()
    today = now.date()
    with tenant_transaction():
        # Only this clinic's own patients: the same rule as the per-patient insurance view.
        everyone = [p["id"] for p in trust.patient_registry()["patients"]]
        mine = trust.people_at_site(everyone, org or "")
        refs = [r for r in everyone if r in mine]
        shared = coverage.shared_with_staff(refs)
        rows = coverage.list_coverage([r for r in refs if shared[r]], include_ended=True)
        upcoming = _upcoming_patients(org or "", now)
    by_person: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_person.setdefault(row["person_ref"], []).append(row)

    people: list[dict[str, Any]] = []
    for ref in refs:
        if shared[ref]:
            state, row = _state(by_person.get(ref, []), today)
        else:
            state, row = NOT_SHARED, None
        people.append({
            "id": ref, "state": state, "upcoming_visit": ref in upcoming,
            "insurer": row["insurer_name"] if row else "", "plan": row["plan_name"] if row else "",
            "valid_until": row["valid_until"] if row else None,
            "member_last4": row["member_last4"] if row else "",
        })
    _add_names(auth, people, "id")

    def count(state: str) -> int:
        return sum(1 for p in people if p["state"] == state)

    # Who the front desk should chase before the visit: nothing in force, about to end, or the
    # patient has not shared (so the clinic cannot tell). Only patients with a visit still to come.
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
