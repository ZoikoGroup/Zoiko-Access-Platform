"""The /api/v1 endpoints used by the web apps.

Each one: logged-in actor -> tenant bound -> policy decision (recorded, even when denied) ->
domain function -> JSON. Errors come out as problem+json from the middleware.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from foundation import fhir, metrics, pagination, synthetic
from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.idempotency import IdempotentOutcome
from foundation.idempotency import run as run_once
from foundation.mail import mail_stats
from foundation.outbox import backlog_stats
from foundation.timeutil import get_zone, utc_now
from modules.ai import api as ai
from modules.billing import api as billing
from modules.catalog import api as catalog
from modules.claim import api as claim
from modules.codemap import api as codemap
from modules.comms import api as comms
from modules.coverage import api as coverage
from modules.evidence import api as evidence
from modules.exchange import api as exchange
from modules.integration import api as integration
from modules.jurisdiction import api as jurisdiction
from modules.messaging import api as messaging
from modules.orchestration import api as orchestration
from modules.policy import api as policy
from modules.programme import api as programme
from modules.readiness import api as readiness
from modules.referral import api as referral
from modules.rights import api as rights
from modules.safety import api as safety
from modules.scheduling import api as scheduling
from modules.sponsor import api as sponsor
from modules.traceability import api as traceability
from modules.trust import api as trust
from modules.waitlist import api as waitlist
from modules.workflow import api as workflow

from .result_holds import IDENTITY_CHECK, identity_held, refuse_if_identity_held
from .support import (
    endpoint,
    held_organizations,
    home_for,
    is_allowed,
    json_body,
    purpose_for,
    require_recent_second_step,
    text,
)

IDEMPOTENCY_RETENTION = dt.timedelta(days=1)


def _authorize(auth: trust.Authenticated, operation: str, **kwargs: Any) -> policy.Decision:
    return policy.authorize(
        actor_public_id=auth.actor_public_id,
        operation=operation,
        purpose_code=purpose_for(auth),
        **kwargs,
    )


def _catalog_org(auth: trust.Authenticated) -> str | None:
    orgs = held_organizations(auth)
    return orgs[0] if orgs else None


def _names(auth: trust.Authenticated, refs: set[str]) -> dict[str, str]:
    """Display names for the patients THIS login may read (the policy engine decides for each).
    Anyone else's name is simply absent, and the apps then show the patient's id."""
    org = _catalog_org(auth)
    allowed = [
        p
        for p in sorted(refs)
        if p and is_allowed(auth, "read:person", organization_public_id=org, subject_ref=p)
    ]
    if not allowed:
        return {}
    with tenant_transaction():
        return trust.profile_summary_for(allowed)


def _add_names(
    auth: trust.Authenticated, rows: list[dict[str, Any]], key: str = "person"
) -> list[dict[str, Any]]:
    """Add `patient_name` to each row whose patient (under `key`) this login may read."""
    names = _names(auth, {str(r[key]) for r in rows if r.get(key)})
    for row in rows:
        row["patient_name"] = names.get(str(row.get(key, ""))) or None
    return rows


def _provider_json(
    slot: dict[str, Any], labels: dict[str, str] | None, verified: set[str] | None = None
) -> dict[str, Any] | None:
    """The doctor on a time, with whether their current name has been checked by the clinic."""
    ref = slot.get("provider_ref")
    if not ref:
        return None
    names = labels if labels is not None else trust.actor_labels()
    checked = verified if verified is not None else trust.verified_providers()
    return {"id": ref, "name": names.get(ref, ""), "verified": ref in checked}


def _booking_json(
    booking: dict[str, Any],
    services: dict[str, Any],
    locations: dict[str, Any],
    orgs: dict[str, str],
    labels: dict[str, str] | None = None,
    verified: set[str] | None = None,
) -> dict[str, Any]:
    slot = booking["slot"]
    service = services.setdefault(slot["service_ref"], catalog.get_service(slot["service_ref"]))
    location = locations.setdefault(
        slot["location_ref"], catalog.get_location(slot["location_ref"])
    )
    return {
        "id": booking["public_id"],
        "status": booking["status"],
        "person": booking["person_ref"],
        "created_at": booking["created_at"],
        "service": {"id": service["public_id"], "name": service["name"], "mode": service["mode"]},
        "organization": {
            "id": slot["organization_ref"],
            "name": orgs.get(slot["organization_ref"], ""),
        },
        "location": {
            "id": location["public_id"],
            "name": location["name"],
            "address": location["address"],
            "phone": location["phone"],
        },
        "starts_at": slot["starts_at"],
        "ends_at": slot["ends_at"],
        "local_start": slot["local_start"],
        "local_zone": slot["local_zone"],
        "provider": _provider_json(slot, labels, verified),
        "rescheduled_from": booking.get("rescheduled_from_ref") or None,
        "attendance": booking.get("attendance") or None,
        "cancel_reason": booking.get("cancel_reason") or None,
        "pending_change": booking.get("pending_change") or None,
        "late": _waiting_too_long(booking),
    }


def _waiting_too_long(booking: dict[str, Any]) -> bool:
    """A booking still waiting for confirmation after the clinic's response target."""
    if booking["status"] != "requested":
        return False
    return catalog.is_late(          # counting only the clinic's opening hours when set (DEV-367)
        booking["slot"]["organization_ref"], dt.datetime.fromisoformat(booking["created_at"]))


def _switched_off(auth: trust.Authenticated) -> list[str]:
    from .capability_gate import switched_off  # noqa: PLC0415 - capability_gate imports support

    return switched_off(auth)


@endpoint("GET", during_set_up=True)
def me(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    with tenant_transaction():
        orgs = trust.organization_names()
        roles = trust.active_roles(auth.actor_public_id)
        persons = trust.reachable_persons(auth.actor_public_id)
        email = trust.email_status(auth.actor_public_id)
        display_name = trust.display_name_of(auth.actor_public_id)
        check = trust.name_check_states(only=auth.actor_public_id).get(
            auth.actor_public_id, {"state": "none"}
        )
    return JsonResponse(
        {
            "actor": auth.actor_public_id,
            "type": auth.actor_type,
            "tenant": auth.tenant_name,
            "cell": auth.context.cell_id,
            "purpose": purpose_for(auth),
            "persons": persons,
            "home": home_for(auth.actor_type, [r for r, _ in roles], auth.tenant_type),
            "email": email,
            "display_name": display_name,
            # Only the state and what was checked: the administrator's note stays with the clinic.
            "name_check": {
                k: check[k] for k in ("state", "basis_text", "checked_on", "reason") if k in check
            },
            "roles": [
                {"role": r, "organization": {"id": o, "name": orgs.get(o, "")}} for r, o in roles
            ],
            # Features this customer's market has not switched on (DEV-383): the apps hide them.
            "switched_off": _switched_off(auth),
        }
    )


def _service_json(s: dict[str, Any], orgs: dict[str, str]) -> dict[str, Any]:
    upcoming = scheduling.list_open_slots(s["public_id"], limit=1)
    return {
        "id": s["public_id"],
        "name": s["name"],
        "category": s["category"],
        "description": s["description"],
        "duration_minutes": s["duration_minutes"],
        "mode": s["mode"],
        "status": s["status"],
        "listing_state": s["listing_state"],
        "organization": {"id": s["organization_ref"], "name": orgs.get(s["organization_ref"], "")},
        "next_slot": upcoming[0]["starts_at"] if upcoming else None,
        "next_slot_local": upcoming[0]["local_start"] if upcoming else None,
        "next_slot_zone": upcoming[0]["local_zone"] if upcoming else None,
    }


@endpoint("GET", "POST")
def services(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    if request.method == "POST":
        return _create_service(request, auth)
    if request.GET.get("all"):
        # Staff view: includes inactive services. Needs the right to change the catalog.
        org = _catalog_org(auth)
        _authorize(auth, "write:catalog", organization_public_id=org)
        with tenant_transaction():
            orgs = trust.organization_names()
            rows = [
                s for s in catalog.list_services(active_only=False) if s["organization_ref"] == org
            ]
            waiting = waitlist.waiting_counts(org or "")
            labels = trust.actor_labels()
            return JsonResponse(
                {
                    "services": [
                        {
                            **_service_json(s, orgs),
                            "waiting": waiting.get(s["public_id"], 0),
                            "listing": {
                                "evidence_note": s["evidence_note"],
                                "requested_by": labels.get(s["requested_by_ref"], ""),
                                "reviewed_by": labels.get(s["reviewed_by_ref"], ""),
                                "you_requested": s["requested_by_ref"] == auth.actor_public_id,
                                "can_approve": "R13" in auth.roles,
                            },
                        }
                        for s in rows
                    ]
                }
            )
    _authorize(auth, "read:catalog", organization_public_id=_catalog_org(auth))
    with tenant_transaction():
        orgs = trust.organization_names()
        mode = request.GET.get("mode", "")
        if mode and mode not in ("in_person", "video"):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["mode"])
        sort = request.GET.get("sort", "name")
        if sort not in ("name", "soonest"):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["sort"])
        rows = catalog.list_services(
            query=request.GET.get("q", "")[:100],
            category=request.GET.get("category", "")[:64],
            mode=mode,
            organization_ref=request.GET.get("organization", ""),
        )
        usual = scheduling.usual_confirmation_hours()
        out = [
            {**_service_json(s, orgs), "usual_confirmation_hours": usual.get(s["organization_ref"])}
            for s in rows
        ]
        if request.GET.get("available"):
            out = [s for s in out if s["next_slot"]]  # only services with a free time
        if sort == "soonest":
            # Free times first, the nearest first; services with none go last, by name.
            out.sort(key=lambda s: (s["next_slot"] is None, s["next_slot"] or "", s["name"]))
        categories = catalog.list_categories()
    return JsonResponse({"services": out, "categories": categories})


def _create_service(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    body = json_body(request)
    org = text(body, "organization")
    duration = body.get("duration_minutes")
    if not isinstance(duration, int) or isinstance(duration, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["duration_minutes"])
    decision = _authorize(auth, "write:catalog", organization_public_id=org)
    with tenant_transaction():
        row = catalog.create_service(
            organization_ref=org,
            name=text(body, "name"),
            category=text(body, "category"),
            duration_minutes=duration,
            mode=text(body, "mode", False) or "in_person",
            description=text(body, "description", False),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
        out = _service_json(row, trust.organization_names())
    return JsonResponse({"service": out}, status=201)


@endpoint("GET", "POST")
def locations(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    if request.method == "POST":
        body = json_body(request)
        org = text(body, "organization")
        decision = _authorize(auth, "write:catalog", organization_public_id=org)
        with tenant_transaction():
            row = catalog.create_location(
                organization_ref=org,
                name=text(body, "name"),
                time_zone=text(body, "time_zone"),
                address=text(body, "address", False),
                phone=text(body, "phone", False),
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"location": _location_json(row)}, status=201)
    wanted = request.GET.get("organization") or _catalog_org(auth)
    _authorize(auth, "read:catalog", organization_public_id=wanted)
    with tenant_transaction():
        rows = catalog.list_locations(organization_ref=wanted or "")
    return JsonResponse({"locations": [_location_json(r) for r in rows]})


@endpoint("POST")
def location_contact(
    request: HttpRequest, auth: trust.Authenticated, location_id: str
) -> HttpResponse:
    """Staff change how patients reach a site: its phone number and address."""
    _staff_only(auth)
    body = json_body(request)
    with tenant_transaction():
        current = catalog.get_location(location_id)
    decision = _authorize(auth, "write:catalog", organization_public_id=current["organization_ref"])
    with tenant_transaction():
        row = catalog.update_location_contact(
            location_ref=location_id,
            phone=text(body, "phone", False),
            address=text(body, "address", False),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"location": _location_json(row)})


def _location_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["public_id"],
        "name": row["name"],
        "address": row["address"],
        "phone": row["phone"],
        "time_zone": row["time_zone"],
        "organization": row["organization_ref"],
    }


@endpoint("POST")
def service_listing(
    request: HttpRequest, auth: trust.Authenticated, service_id: str
) -> HttpResponse:
    """Ask for a listing, review it, publish it or withdraw it (GHE-14 WF-04). A service can be
    booked only once it is published. Reviewing, publishing and withdrawing are for a clinic
    administrator, and never for the person who asked for the listing."""
    body = json_body(request)
    with tenant_transaction():
        org = catalog.get_service(service_id)["organization_ref"]
    decision = _authorize(auth, "write:catalog", organization_public_id=org)
    with tenant_transaction():
        row = catalog.change_listing(
            service_id,
            text(body, "action"),
            note=text(body, "note", False),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
        out = _service_json(row, trust.organization_names())
    return JsonResponse({"service": out})


@endpoint("POST")
def service_status(
    request: HttpRequest, auth: trust.Authenticated, service_id: str
) -> HttpResponse:
    body = json_body(request)
    with tenant_transaction():
        org = catalog.get_service(service_id)["organization_ref"]
    decision = _authorize(auth, "write:catalog", organization_public_id=org)
    with tenant_transaction():
        row = catalog.set_service_status(
            service_id,
            text(body, "status"),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
        out = _service_json(row, trust.organization_names())
    return JsonResponse({"service": out})


def _add_slots(request: HttpRequest, auth: trust.Authenticated, service_id: str) -> HttpResponse:
    body = json_body(request)
    count = body.get("count")
    step = body.get("step_minutes")
    if not isinstance(count, int) or isinstance(count, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["count"])
    if step is not None and (not isinstance(step, int) or isinstance(step, bool)):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["step_minutes"])
    days = body.get("days", 1)
    weekdays_only = body.get("weekdays_only", False)
    if not isinstance(days, int) or isinstance(days, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["days"])
    if not isinstance(weekdays_only, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["weekdays_only"])
    try:
        first = dt.datetime.fromisoformat(text(body, "first_local"))
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["first_local"]) from None
    with tenant_transaction():
        org = catalog.get_service(service_id)["organization_ref"]
    decision = _authorize(auth, "write:catalog", organization_public_id=org)
    with tenant_transaction():
        made = scheduling.create_slots(
            service_ref=service_id,
            location_ref=text(body, "location"),
            first_local=first,
            count=count,
            step_minutes=step,
            provider_ref=text(body, "provider", False),
            days=days,
            weekdays_only=weekdays_only,
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"created": len(made)}, status=201)


@endpoint("POST")
def slot_withdraw(request: HttpRequest, auth: trust.Authenticated, slot_id: str) -> HttpResponse:
    """Take one free future time off the calendar. Refused if it has a booking."""
    with tenant_transaction():
        org = scheduling.get_slot(slot_id)["organization_ref"]
    decision = _authorize(auth, "write:catalog", organization_public_id=org)
    with tenant_transaction():
        scheduling.withdraw_slot(
            slot_ref=slot_id, actor_public_id=auth.actor_public_id, decision=decision
        )
    return JsonResponse({"withdrawn": True})


@endpoint("POST")
def service_slots_withdraw(
    request: HttpRequest, auth: trust.Authenticated, service_id: str
) -> HttpResponse:
    """Take free future times off the calendar for a range of days, for one doctor if given.
    `dry_run: true` only counts. Times with a booking are never touched."""
    body = json_body(request)
    dry_run = body.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["dry_run"])
    try:
        first = dt.date.fromisoformat(text(body, "first_day"))
        last = dt.date.fromisoformat(text(body, "last_day"))
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["first_day", "last_day"]) from None
    with tenant_transaction():
        org = catalog.get_service(service_id)["organization_ref"]
    decision = _authorize(auth, "write:catalog", organization_public_id=org)
    with tenant_transaction():
        count = scheduling.withdraw_slots(
            service_ref=service_id,
            first_day=first,
            last_day=last,
            provider_ref=text(body, "provider", False),
            dry_run=dry_run,
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"count": count, "dry_run": dry_run})


@endpoint("GET", "POST")
def service_slots(request: HttpRequest, auth: trust.Authenticated, service_id: str) -> HttpResponse:
    if request.method == "POST":
        return _add_slots(request, auth, service_id)
    _authorize(auth, "read:catalog", organization_public_id=_catalog_org(auth))
    with tenant_transaction():
        service = catalog.get_service(service_id)
        slots = scheduling.list_open_slots(service_id)
        names = {s["location_ref"]: catalog.get_location(s["location_ref"])["name"] for s in slots}
        people = trust.actor_labels()
        checked = trust.verified_providers()
    return JsonResponse(
        {
            "service": {
                "id": service["public_id"],
                "name": service["name"],
                "duration_minutes": service["duration_minutes"],
            },
            "slots": [
                {
                    "id": s["public_id"],
                    "starts_at": s["starts_at"],
                    "ends_at": s["ends_at"],
                    "local_start": s["local_start"],
                    "local_zone": s["local_zone"],
                    "location": names[s["location_ref"]],
                    "provider": _provider_json(s, people, checked),
                }
                for s in slots
            ],
        }
    )


@endpoint("GET", "POST")
def bookings(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    if request.method == "POST":
        return _create_booking(request, auth)
    return _list_bookings(auth)


def _list_bookings(auth: trust.Authenticated) -> HttpResponse:
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:booking", subject_ref=p)]
    seen: dict[str, dict[str, Any]] = {}
    with tenant_transaction():
        if persons:
            for b in scheduling.list_bookings(person_refs=persons):
                seen[b["public_id"]] = b
    for org in held_organizations(auth):
        if is_allowed(auth, "read:booking", organization_public_id=org):
            with tenant_transaction():
                for b in scheduling.list_bookings(organization_ref=org):
                    seen[b["public_id"]] = b
    with tenant_transaction():
        orgs = trust.organization_names()
        svc: dict[str, Any] = {}
        loc: dict[str, Any] = {}
        names = trust.actor_labels()
        checked = trust.verified_providers()
        rows = [_booking_json(b, svc, loc, orgs, names, checked) for b in seen.values()]
    rows.sort(key=lambda r: r["starts_at"])
    return JsonResponse({"bookings": _add_names(auth, rows)})


def _create_booking(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    body = json_body(request)
    slot_ref = text(body, "slot")
    key = request.headers.get("Idempotency-Key", "")
    with tenant_transaction():
        person = text(body, "person", required=False)
        if not person:
            mine = trust.reachable_persons(auth.actor_public_id)
            if len(mine) != 1:  # none, or several: the caller must say which
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["person"])
            person = mine[0]
        slot = scheduling.get_slot(slot_ref)
    decision = _authorize(
        auth, "write:booking", organization_public_id=slot["organization_ref"], subject_ref=person
    )
    with tenant_transaction():
        outcome = run_once(
            scope=f"actor:{auth.actor_public_id} POST /bookings",
            key=key,
            payload=body,
            retain_for=IDEMPOTENCY_RETENTION,
            work=lambda: {
                "booking": scheduling.book(
                    slot_ref=slot_ref,
                    person_ref=person,
                    actor_public_id=auth.actor_public_id,
                    decision=decision,
                )["public_id"]
            },
        )
        booking = scheduling.get_booking(str(outcome.result["booking"]))
        orgs = trust.organization_names()
        out = _booking_json(booking, {}, {}, orgs)
    response = JsonResponse({"booking": out}, status=200 if outcome.replayed else 201)
    response["Idempotent-Replayed"] = "true" if outcome.replayed else "false"
    return response


def _act_on_booking(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str, verb: str
) -> HttpResponse:
    with tenant_transaction():
        booking = scheduling.get_booking(booking_id)
    org = booking["slot"]["organization_ref"]
    decision = _authorize(
        auth, "write:booking", organization_public_id=org, subject_ref=booking["person_ref"]
    )
    with tenant_transaction():
        if verb == "confirm":
            done = scheduling.confirm(
                booking_ref=booking_id, actor_public_id=auth.actor_public_id, decision=decision
            )
        elif verb == "keep":     # the patient takes back a cancel request (DEV-390)
            done = scheduling.keep(
                booking_ref=booking_id, actor_public_id=auth.actor_public_id, decision=decision
            )
        else:
            reason = text(json_body(request), "reason", required=False)
            done = scheduling.cancel(
                booking_ref=booking_id,
                actor_public_id=auth.actor_public_id,
                decision=decision,
                reason=reason,
            )
        out = _booking_json(done, {}, {}, trust.organization_names())
    return JsonResponse({"booking": out})


@endpoint("POST")
def booking_confirm(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str
) -> HttpResponse:
    return _act_on_booking(request, auth, booking_id, "confirm")


@endpoint("POST")
def booking_cancel(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str
) -> HttpResponse:
    return _act_on_booking(request, auth, booking_id, "cancel")


@endpoint("POST")
def booking_keep(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str
) -> HttpResponse:
    """The patient takes back their request to cancel, before the clinic answers (DEV-390)."""
    return _act_on_booking(request, auth, booking_id, "keep")


def _ics_text(value: str) -> str:
    """Escape text for an iCalendar line (RFC 5545): backslash, semicolon, comma, line breaks."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", " ")
        .replace("\n", " ")
    )


_EVENT_WORDS = {  # what the step is called, the same for everyone
    "requested": "Requested",
    "confirmed": "Confirmed",
    "cancelled": "Cancelled",
    "moved_away": "Moved to another time",
    "moved_here": "Booked as the new time",
    "attended": "Attended",
    "no_show": "Did not attend",
    "reminder_sent": "Reminder sent",
    "cancel_requested": "Cancellation asked for: waiting for the clinic",
    "change_requested": "New time asked for: waiting for the clinic",
    "change_withdrawn": "Change not made: this appointment stays",
}


def _who(source: str, actor: str, patient_side: bool, labels: dict[str, str]) -> str:
    """Who acted, in words the reader may see. A patient never sees which staff member acted,
    and clinic staff see the patient side only as "the patient or family", never a name."""
    if source == "system":
        return "Automatic"
    if source == "patient":
        return "You or your family" if patient_side else "The patient or family"
    return "The clinic" if patient_side else labels.get(actor, "Clinic staff")


def _history_payload(auth: trust.Authenticated, booking_id: str) -> dict[str, Any]:
    """The steps of one appointment as THIS reader may see them (checks the right to read it)."""
    with tenant_transaction():
        booking = scheduling.get_booking(booking_id)
    org = booking["slot"]["organization_ref"]
    _authorize(auth, "read:booking", organization_public_id=org, subject_ref=booking["person_ref"])
    patient_side = bool(set(auth.roles) & {"R01", "R02"})
    with tenant_transaction():
        data = scheduling.booking_history(booking_id)
        labels = {} if patient_side else trust.actor_labels()
    return {
        "chain_ok": data["chain_ok"],
        "events": [
            {
                "seq": e["seq"],
                "event": e["event_type"],
                "text": _EVENT_WORDS[e["event_type"]],
                "from": e["state_from"],
                "to": e["state_to"],
                "reason": e["reason_code"],
                "who": _who(e["source"], e["actor_ref"], patient_side, labels),
                "at": e["occurred_at"],
            }
            for e in data["events"]
        ],
    }


@endpoint("GET")
def booking_history(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str
) -> HttpResponse:
    """Every step of an appointment, oldest first, and whether the record still checks out.
    Bookings made before the history was kept have none."""
    return JsonResponse(_history_payload(auth, booking_id))


@endpoint("GET")
def booking_calendar(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str
) -> HttpResponse:
    """The appointment as a calendar file (.ics) the patient can add to any calendar app. It holds
    the service, the clinic, the place and the time: no patient name or any other personal data."""
    with tenant_transaction():
        booking = scheduling.get_booking(booking_id)
    org = booking["slot"]["organization_ref"]
    _authorize(auth, "read:booking", organization_public_id=org, subject_ref=booking["person_ref"])
    if booking["status"] == "cancelled":
        raise ZoikoError(ErrorCode.NOT_FOUND)  # nothing to add to a calendar
    with tenant_transaction():
        row = _booking_json(booking, {}, {}, trust.organization_names())
    stamp = "%Y%m%dT%H%M%SZ"
    start = dt.datetime.fromisoformat(row["starts_at"]).astimezone(dt.timezone.utc)
    end = dt.datetime.fromisoformat(row["ends_at"]).astimezone(dt.timezone.utc)
    place = (row["location"]["name"] + " " + row["location"]["address"]).strip()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Zoiko Access//Appointments//EN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{row['id']}@zoikoaccess",
        f"DTSTAMP:{utc_now().strftime(stamp)}",
        f"DTSTART:{start.strftime(stamp)}",
        f"DTEND:{end.strftime(stamp)}",
        f"SUMMARY:{_ics_text(row['service']['name'] + ' at ' + row['organization']['name'])}",
        f"LOCATION:{_ics_text(place)}",
        "STATUS:" + ("CONFIRMED" if row["status"] == "confirmed" else "TENTATIVE"),
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    response = HttpResponse("\r\n".join(lines) + "\r\n", content_type="text/calendar")
    response["Content-Disposition"] = 'attachment; filename="appointment.ics"'
    return response


@endpoint("POST")
def booking_reschedule(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str
) -> HttpResponse:
    """Move an appointment to another free time of the same service and organization."""
    body = json_body(request)
    new_slot = text(body, "slot")
    with tenant_transaction():
        booking = scheduling.get_booking(booking_id)
    org = booking["slot"]["organization_ref"]
    decision = _authorize(
        auth, "write:booking", organization_public_id=org, subject_ref=booking["person_ref"]
    )
    with tenant_transaction():
        done = scheduling.reschedule(
            booking_ref=booking_id,
            new_slot_ref=new_slot,
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
        out = _booking_json(done, {}, {}, trust.organization_names(), trust.actor_labels())
    return JsonResponse({"booking": out})


@endpoint("POST")
def booking_attendance(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str
) -> HttpResponse:
    """Clinic staff record whether the patient attended a confirmed appointment."""
    body = json_body(request)
    outcome = text(body, "outcome")
    with tenant_transaction():
        booking = scheduling.get_booking(booking_id)
    decision = _authorize(
        auth,
        "write:booking",
        organization_public_id=booking["slot"]["organization_ref"],
        subject_ref=booking["person_ref"],
    )
    with tenant_transaction():
        done = scheduling.mark_attendance(
            booking_ref=booking_id,
            outcome=outcome,
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
        out = _booking_json(done, {}, {}, trust.organization_names(), trust.actor_labels())
    return JsonResponse({"booking": out})


# ------------------------------------------------------------------ sign in and out
@csrf_exempt
@require_POST
def login(request: HttpRequest) -> HttpResponse:
    """Organization + login + password -> a signed token and where this person belongs.
    Every failure is the same 401, whatever the reason."""
    body = json_body(request)
    password = body.get("password")
    if not isinstance(password, str):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["password"])
    result = trust.login(
        tenant_name=text(body, "organization"),
        login=text(body, "login"),
        password=password,
        remember=body.get("remember") is True,
    )
    if result.second_step_ticket:
        # Two-step sign-in (DEV-380): the password was right, the code is still to come. No
        # session, no roles: only a 5-minute ticket for POST auth/login/second-step.
        response = JsonResponse({
            "second_step": True, "ticket": result.second_step_ticket,
            "expires_at": result.expires_at.isoformat()})
    else:
        response = JsonResponse(_login_json(result))
    response["Cache-Control"] = "no-store"
    return response


def _login_json(result: trust.LoginResult) -> dict[str, Any]:
    return {
        "token": result.token,
        "must_set_up_two_step": result.must_set_up_two_step,
        "expires_at": result.expires_at.isoformat(),
        "remembered": result.remembered,
        "home": home_for(result.actor_type, [r for r, _, _ in result.roles], result.tenant_type),
        "actor": {"id": result.actor_public_id, "type": result.actor_type, "login": result.login},
        "tenant": {"id": result.tenant_public_id, "name": result.tenant_name},
        "roles": [{"role": r, "organization": {"id": o, "name": n}} for r, o, n in result.roles],
    }


@csrf_exempt
@require_POST
def register(request: HttpRequest) -> HttpResponse:
    """A patient creates their own account, if the organization allows it. Signs them in."""
    body = json_body(request)
    password = body.get("password")
    if not isinstance(password, str):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["password"])
    result = trust.register_patient(
        tenant_name=text(body, "organization"), login=text(body, "login"), password=password
    )
    response = JsonResponse(_login_json(result), status=201)
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_GET
def openapi_document(request: HttpRequest) -> HttpResponse:
    """The OpenAPI description of this API (open to read; it holds no data)."""
    from .openapi import build_spec

    return JsonResponse(build_spec())


@csrf_exempt
@require_GET
def asyncapi_document(request: HttpRequest) -> HttpResponse:
    """The AsyncAPI description of the events Zoiko publishes (open to read; it holds no data)."""
    from foundation.event_catalog import build_asyncapi

    return JsonResponse(build_asyncapi())


@csrf_exempt
@require_GET
def providers(request: HttpRequest) -> HttpResponse:
    """Which sign-in options this server offers (so the page shows only real ones)."""
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    return JsonResponse({"google": {"enabled": bool(client_id), "client_id": client_id or None}})


@endpoint("POST", during_set_up=True)
def logout(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Sign out everywhere: every token this person was ever issued stops working."""
    with tenant_transaction():
        trust.revoke_sessions(auth.actor_public_id, by=auth.actor_public_id)
    return JsonResponse({"signed_out": True})


# ------------------------------------------------------------------ staff screens
def _staff_only(auth: trust.Authenticated, *extra_areas: str) -> str | None:
    """These endpoints serve the staff console: only operations-area accounts may use them,
    whatever else the policy table would allow. Returns the organization to check against."""
    area = home_for(auth.actor_type, list(auth.roles), auth.tenant_type)
    if area not in ("operations", "platform", *extra_areas):  # platform staff see their own tenant
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return _catalog_org(auth)


def _evidence_block(auth: trust.Authenticated, org: str | None) -> dict[str, Any]:
    """Recent access decisions, audit entries and the chain check. Empty if not permitted."""
    if not is_allowed(auth, "read:evidence", organization_public_id=org):
        return {"evidence": False, "decisions": [], "events": [], "chain": None}
    with tenant_transaction():
        labels = trust.actor_labels()
        decisions = [
            {
                "at": d["at"],
                "actor": labels.get(str(d["actor"]), str(d["actor"])),
                "operation": d["operation"],
                "purpose": d["purpose"],
                "allowed": d["allowed"],
                "reason": d["reason"],
            }
            for d in policy.recent_decisions()
        ]
        events = [
            {**e, "actor": labels.get(e["actor"], e["actor"])} for e in evidence.recent_events()
        ]
        report = evidence.verify_chain()
    return {
        "evidence": True,
        "decisions": decisions,
        "events": events,
        "chain": {
            "ok": report.ok,
            "checked": report.checked,
            "problem": report.problem,
            "bad_seq": report.first_bad_seq,
        },
    }


@endpoint("GET")
def staff_dashboard(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    _authorize(auth, "read:analytics", organization_public_id=org)
    # results to review (DEV-165); held requests (DEV-169); held too long, for the administrator
    partner_results = partner_on_hold = partner_held_mine = partner_escalated = 0
    if org is not None and any(
        t["type"] == "platform" and t["status"] == "active" for t in trust.list_tenants()
    ):
        with tenant_transaction(_platform_context()):
            mine = exchange.clinic_counts(_my_customer(auth))
            partner_results, partner_on_hold = mine["to_review"], mine["on_hold"]
            partner_held_mine = exchange.clinic_counts(_my_customer(auth), auth.actor_public_id)[
                "on_hold_mine"
            ]
            if "R13" in auth.roles:  # the administrator's own number (DEV-303)
                partner_escalated = mine["escalated"]
    with tenant_transaction():
        numbers = trust.counts()
        attention = {
            "bookings_to_confirm": scheduling.booking_counts()["requested"],
            "family_requests": trust.pending_family_count(),
            "waiting_list": sum(waitlist.waiting_counts(org or "").values()),
            "messages": 0 if org is None else messaging.unread_threads_for_staff(org),
            "referrals_to_decide": 0
            if org is None
            else sum(
                1
                for r in referral.list_referrals(destination_organization_ref=org)
                if r["state"] == "issued"
            ),
            "data_requests": 0 if org is None else rights.open_count(org),
            "workflows_without_owner": 0 if org is None else orchestration.ownerless_count(org),
            "partner_results": partner_results,
            "partner_on_hold": partner_on_hold,
            "partner_held_mine": partner_held_mine,
            "partner_hold_escalated": partner_escalated,
            # an administrator's number: webhook deliveries that gave up and need a person
            "webhooks_gave_up": integration.gave_up_count() if "R13" in auth.roles else 0,
        }
        today = (
            scheduling.today_counts(org)
            if org
            else {"confirmed": 0, "waiting": 0, "still_to_come": 0}
        )
        target = trust.response_target_hours(org) if org else settings.RESPONSE_TARGET_HOURS
        # The clinic's own "late" line, counting only its opening hours when set (DEV-367).
        cutoff = catalog.late_cutoff(org) if org else None
        late = {
            "hours": target,
            "bookings": scheduling.overdue_requests(target, organization_ref=org or "",
                                                    cutoff=cutoff),
            "messages": 0 if org is None else messaging.overdue_threads_for_staff(
                org, target, cutoff=cutoff),
            "data_requests": 0 if org is None else rights.overdue_count(
                org, target, cutoff=cutoff),
        }
    return JsonResponse(
        {
            "tenant": auth.tenant_name,
            "counts": numbers,
            "attention": attention,
            "late": late,
            "today": today,
            **_evidence_block(auth, org),
        }
    )


def _work_json(rows: list[dict[str, Any]], labels: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "id": r["public_id"],
            "kind": r["kind"],
            "label": r["label"],
            "subject": r["subject_ref"],
            "created_at": r["created_at"],
            "due_at": r["due_at"],
            "status": r["status"],
            "closed_at": r["closed_at"],
            "overdue": r["overdue"],
            "assigned_to": r["assigned_to_ref"],
            "assigned_to_name": labels.get(r["assigned_to_ref"], "")
            if r["assigned_to_ref"]
            else "",
        }
        for r in rows
    ]


@endpoint("GET")
def staff_work(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The clinic's open work, the one due first at the top. `?mine=1` or `?unassigned=1` narrows
    it. `?closed=<days>` (1 to 30) lists work closed in that many days instead, newest first. Kinds
    of work and times only: nothing about the patient."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:analytics", organization_public_id=org)
    if "closed" in request.GET:
        try:
            days = int(request.GET["closed"])
        except ValueError:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["closed"]) from None
        if not 1 <= days <= workflow.MAX_CLOSED_DAYS:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["closed"])
        with tenant_transaction():
            closed = workflow.list_closed(org, days)
            labels = trust.actor_labels()
        return JsonResponse(
            {"me": auth.actor_public_id, "closed_days": days, "items": _work_json(closed, labels)}
        )
    with tenant_transaction():
        rows = workflow.list_items(
            org,
            assigned_to_ref=auth.actor_public_id if request.GET.get("mine") else "",
            unassigned=bool(request.GET.get("unassigned")),
        )
        labels = trust.actor_labels()
    return JsonResponse({"me": auth.actor_public_id, "items": _work_json(rows, labels)})


def _work_action(auth: trust.Authenticated, item_id: str, verb: str) -> HttpResponse:
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:analytics", organization_public_id=org)
    act = workflow.claim if verb == "claim" else workflow.release
    with tenant_transaction():
        done = act(item_id, org, auth.actor_public_id)
        labels = trust.actor_labels()
    return JsonResponse({"item": _work_json([done], labels)[0]})


@endpoint("POST")
def staff_work_claim(request: HttpRequest, auth: trust.Authenticated, item_id: str) -> HttpResponse:
    return _work_action(auth, item_id, "claim")


@endpoint("POST")
def staff_work_release(
    request: HttpRequest, auth: trust.Authenticated, item_id: str
) -> HttpResponse:
    return _work_action(auth, item_id, "release")


_WORK_WORDS = {
    "opened": "Work arrived",
    "claimed": "Taken",
    "released": "Given back",
    "done": "Done",
    "cancelled": "No longer needed",
}


@endpoint("GET")
def staff_work_history(
    request: HttpRequest, auth: trust.Authenticated, item_id: str
) -> HttpResponse:
    """Every step of one work item of this clinic, oldest first, and whether the record still
    checks out. Kinds of work and who acted: nothing about a patient."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:analytics", organization_public_id=org)
    with tenant_transaction():
        data = workflow.item_history(item_id, org)
        labels = trust.actor_labels()
    return JsonResponse(
        {
            "chain_ok": data["chain_ok"],
            "status": data["status"],
            "events": [
                {
                    "seq": e["seq"],
                    "event": e["event_type"],
                    "text": _WORK_WORDS[e["event_type"]],
                    "who": (
                        "Automatic"
                        if e["actor_ref"] == "system"
                        else "The patient or family"
                        if e["actor_ref"] == "patient"
                        else labels.get(e["actor_ref"], "Clinic staff")
                    ),
                    "at": e["occurred_at"],
                }
                for e in data["events"]
            ],
        }
    )


@endpoint("GET")
def staff_providers(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The clinic's doctors with their load for the next 14 days. Numbers and times only."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:analytics", organization_public_id=org)
    with tenant_transaction():
        refs = trust.clinicians_at(org)
        labels = trust.actor_labels()
        load = scheduling.provider_load(org, refs)
        checks = trust.name_check_states()
    return JsonResponse(
        {
            "days": 14,
            "can_manage": "R13" in auth.roles,
            "bases": trust.NAME_CHECK_BASES,
            "providers": [
                {
                    "id": ref,
                    "name": labels.get(ref, ""),
                    "name_check": checks.get(ref, {"state": "none"}),
                    **load[ref],
                }
                for ref in refs
            ],
        }
    )


def _name_check_admin(auth: trust.Authenticated) -> str:
    """TEMPORARY RULE (DEV-095): one organization administrator (R13) may verify or withdraw the
    name check of a doctor at their own organization. Open question 25 (backlog B7)."""
    org = _staff_only(auth)
    if org is None or "R13" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return org


@endpoint("POST")
def staff_provider_verify(
    request: HttpRequest, auth: trust.Authenticated, provider_id: str
) -> HttpResponse:
    org = _name_check_admin(auth)
    body = json_body(request)
    with tenant_transaction():
        done = trust.verify_name(
            provider_id,
            by_ref=auth.actor_public_id,
            organization_ref=org,
            basis=text(body, "basis"),
            note=text(body, "note", False),
        )
    return JsonResponse({"name_check": done})


@endpoint("POST")
def staff_provider_unverify(
    request: HttpRequest, auth: trust.Authenticated, provider_id: str
) -> HttpResponse:
    org = _name_check_admin(auth)
    body = json_body(request)
    with tenant_transaction():
        done = trust.withdraw_check(
            provider_id,
            by_ref=auth.actor_public_id,
            organization_ref=org,
            reason=text(body, "reason"),
        )
    return JsonResponse({"name_check": done})


@endpoint("GET", "POST")
def staff_settings(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The rules this deployment runs by (read only), and how many people would get the daily
    late-work email, so staff can see when nobody would. Numbers only: no addresses."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:analytics", organization_public_id=org)
    if request.method == "POST":
        # Only the clinic's administrator sets its own response target (1 to 168 hours, or null
        # to go back to the service default).
        if "R13" not in auth.roles:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        _authorize(auth, "write:staff", organization_public_id=org)
        sent = json_body(request)
        with tenant_transaction():
            # The clinic's own waiting-list offer window (DEV-366), and/or its response target.
            if "waitlist_offer_hours" in sent:
                waitlist.set_offer_hours(
                    org, sent["waitlist_offer_hours"], by=auth.actor_public_id)
            if "hours" in sent or "waitlist_offer_hours" not in sent:
                trust.set_response_target(org, sent.get("hours"), by=auth.actor_public_id)
    with tenant_transaction():
        recipients = len(trust.verified_emails_for_role(org, "R04"))
        target = trust.response_target_hours(org)
        offer_hours = waitlist.offer_hours_for(org)
    return JsonResponse(
        {
            "response_target_hours": target,
            "response_target_default": settings.RESPONSE_TARGET_HOURS,
            "can_set_target": "R13" in auth.roles,
            "reminder_lead_hours": settings.REMINDER_LEAD_HOURS,
            "waitlist_offer_hours": offer_hours,
            "waitlist_offer_default": settings.WAITLIST_OFFER_HOURS,
            "late_email_recipients": recipients,
        }
    )


@endpoint("GET", "POST")
def staff_business_hours(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the clinic's working days and opening times (or null when not set), and the clock they
    are read on. POST {"business_hours": {...} | null}: set or clear them (the clinic administrator
    only). Every late flag, work-queue deadline and late count then counts only open hours
    (DEV-367, from the maruthi branch)."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:analytics", organization_public_id=org)
    if request.method == "POST":
        if "R13" not in auth.roles:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        _authorize(auth, "write:staff", organization_public_id=org)
        body = json_body(request)
        if "business_hours" not in body:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["business_hours"])
        with tenant_transaction():
            trust.set_business_hours(org, body["business_hours"], by=auth.actor_public_id)
    with tenant_transaction():
        current = trust.business_hours_of(org)
        zone = catalog.organization_zone(org)
    return JsonResponse({
        "business_hours": current, "zone": zone["zone"], "zone_source": zone["source"],
        "can_set": "R13" in auth.roles})


@endpoint("GET")
def staff_compliance(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    _authorize(auth, "read:evidence", organization_public_id=org)
    with tenant_transaction():
        labels = trust.actor_labels()
        summary = policy.denial_summary()
    summary["by_person"] = [
        {**p, "actor": labels.get(str(p["actor"]), str(p["actor"]))} for p in summary["by_person"]
    ]
    return JsonResponse({**_evidence_block(auth, org), "refused": summary})


@endpoint("GET")
def staff_directory(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    _authorize(auth, "read:directory", organization_public_id=org)
    with tenant_transaction():
        data = trust.staff_directory()
    # Only a hint for the screen (whether to show the buttons). The server enforces every change.
    data["can_manage"] = "R13" in auth.roles
    data["me"] = auth.actor_public_id
    data["grantable_roles"] = [
        {"code": code, "label": label.removeprefix("a ").removeprefix("an ")}
        for code, (_, label) in trust.INVITABLE_ROLES.items()
    ]
    return JsonResponse(data)


@endpoint("GET")
def staff_patients(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    _authorize(auth, "read:person", organization_public_id=_staff_only(auth))
    with tenant_transaction():
        registry = trust.patient_registry()
    _add_names(auth, registry["patients"], "id")
    return JsonResponse(registry)


@endpoint("GET")
def staff_reports(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Operational numbers for this organization over the last 7 to 90 days. Counts only: no
    patient, name or message is identified. Needs the analytics right."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:analytics", organization_public_id=org)
    try:
        days = int(request.GET.get("days", "30"))
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["days"]) from None
    if not 7 <= days <= 90:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["days"])
    with tenant_transaction():
        clock = catalog.organization_zone(org)
    # Days run midnight to midnight on the clinic's own clock (UTC when it has no single one).
    zone = get_zone(clock["zone"])
    today = utc_now().astimezone(zone).date()
    midnight = dt.time.min
    since = dt.datetime.combine(today - dt.timedelta(days=days - 1), midnight, tzinfo=zone)
    until = dt.datetime.combine(today + dt.timedelta(days=1), midnight, tzinfo=zone)
    since, until = since.astimezone(dt.timezone.utc), until.astimezone(dt.timezone.utc)
    with tenant_transaction():
        bookings = scheduling.report(org, since, until, clock["zone"])
        people = trust.actor_labels()
        names = {s["public_id"]: s["name"] for s in catalog.list_services(active_only=False)}
        out = {
            "days": days,
            "from": since.astimezone(zone).date().isoformat(),
            "to": (until - dt.timedelta(seconds=1)).astimezone(zone).date().isoformat(),
            "zone": clock["zone"],
            "zone_source": clock["source"],
            "bookings": {
                **bookings,
                "busiest_services": [
                    {"name": names.get(b["service_ref"], "A service"), "count": b["count"]}
                    for b in bookings["busiest_services"]
                ],
                "by_doctor": [
                    {
                        **d,
                        "name": people.get(d["provider_ref"], "A doctor")
                        if d["provider_ref"]
                        else "No doctor chosen",
                    }
                    for d in bookings["by_doctor"]
                ],
            },
            "forecast": scheduling.forecast_week(org),
            "referrals": referral.report(org, since, until),
            "waiting_list": waitlist.report(org, since, until),
            "messages": messaging.report(org, since, until),
        }
    # Approved measures only, small numbers suppressed, the run recorded (DEV-389).
    from .report_measures import measured

    return JsonResponse(measured(auth, org, out))


# ------------------------------------------------------------------ platform console (Zoiko staff)
def _platform_only(auth: trust.Authenticated) -> str | None:
    """Only Zoiko's own staff: an account in a tenant of type `platform` (customers cannot create
    one) holding R11 or R12. The policy engine is asked as well; all three must agree."""
    if auth.tenant_type != "platform":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if not set(auth.roles) & {"R11", "R12"}:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return _catalog_org(auth)


@endpoint("GET")
def platform_delivery_attempts(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Regional support's view of delivery attempts across every customer in this region (GHE-13
    T-54, DEV-328): per customer, counts by state and the attempts not yet delivered, STATUS ONLY:
    never a message, its fingerprint, the receiving address or a person. Only Zoiko's own staff
    (R11, R12). Each look is recorded in the platform's audit chain."""
    from modules.integration import api as integration

    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    tenants = [t for t in trust.list_tenants() if t["type"] != "platform"]
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action="platform.delivery_attempts_viewed",
            object_type="platform",
            object_ref="delivery_attempts",
            outcome=evidence.AuditOutcome.ALLOWED,
            details={"tenants": len(tenants)},
        )
    out = []
    for t in tenants:
        with tenant_transaction(trust.context_for(t["public_id"])):
            seen = integration.support_attempts()
        if any(seen["counts"].values()) or seen["fhir_held"]:
            out.append({"id": t["public_id"], "name": t["name"], "status": t["status"], **seen})
    out.sort(key=lambda r: (-r["counts"].get("dead_letter", 0), -r["counts"].get("retrying", 0),
                            r["name"]))
    return JsonResponse({"customers": out})


@endpoint("GET")
def platform_overview(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """One row per customer tenant with AGGREGATE numbers only: no patient rows, names or emails.
    Each view is recorded in the platform tenant's own audit chain."""
    from .capability_gate import all_capabilities
    from .capability_gate import mode as gating_mode

    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    tenants = trust.list_tenants()
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action="platform.overview",
            object_type="platform",
            object_ref="overview",
            outcome=evidence.AuditOutcome.ALLOWED,
            details={"tenants": len(tenants)},
        )
    all_caps = all_capabilities()
    with tenant_transaction():
        incidents = [i for i in trust.list_incidents() if i["status"] == "open"]
        waiting = [s for s in trust.list_changes() if s["status"] == "pending"]
        latest = trust.list_releases(limit=1)
        hazard_gaps = safety.trace()["gaps"]
        caps_by_country = {
            c: jurisdiction.enabled_capabilities(c)
            for c in {t["country"] for t in tenants if t.get("country")}
        }
    platform_work = {
        "open_incidents": len(incidents),
        # Open incidents that are SEV-0 or SEV-1 (the key keeps its older name "high").
        "high_incidents": sum(1 for i in incidents if i["severity"] in ("sev0", "sev1")),
        "suspensions_waiting": len(waiting),
        "missing_control_tests": hazard_gaps["tests_missing"],
        "unverified_controls": hazard_gaps["controls_waiting"],
        "latest_release": (
            {"version": latest[0]["version"], "released_on": latest[0]["released_on"]}
            if latest
            else None
        ),
    }
    now = utc_now()
    since = now - dt.timedelta(hours=24)
    rows = []
    for t in tenants:
        on = (
            caps_by_country.get(t["country"], frozenset()) & all_caps
            if t["type"] != "platform" and t.get("country")
            else frozenset()
        )
        caps = (
            {"on": sorted(on), "off": sorted(all_caps - on)}
            if t["type"] != "platform"
            else {"on": [], "off": []}
        )
        with tenant_transaction(trust.context_for(t["public_id"])):
            rows.append(
                {
                    "id": t["public_id"],
                    "name": t["name"],
                    "type": t["type"],
                    "status": t["status"],
                    "self_registration": t["self_registration"] == "true",
                    "country": t["country"],
                    "capabilities": caps,
                    "created_at": t["created_at"],
                    "counts": trust.counts(),
                    "services": catalog.service_counts(),
                    "bookings": scheduling.booking_counts(),
                    "audit": evidence.activity_summary(since),
                    "decisions": policy.decision_counts(since),
                }
            )
    return JsonResponse(
        {
            "cell": auth.context.cell_id,
            "generated_at": now.isoformat(),
            "pack_gating": gating_mode(),
            "outbox": backlog_stats(),
            "mail": mail_stats(),
            "work": platform_work,
            "tenants": rows,
        }
    )


@endpoint("GET", "POST")
def platform_releases(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The platform's release log. Zoiko support and safety staff may read it; only those who may
    write the platform (support) may add an entry."""
    org = _platform_only(auth)
    if request.method == "POST":
        body = json_body(request)
        try:
            day = dt.date.fromisoformat(text(body, "released_on"))
        except ValueError:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["released_on"]) from None
        decision = _authorize(auth, "write:platform", organization_public_id=org)
        with tenant_transaction():
            made = trust.add_release(
                version=body.get("version"),
                summary=body.get("summary"),
                details=body.get("details", ""),
                released_on=day,
                actor_public_id=auth.actor_public_id,
                decision=decision,
                organization_public_id=org,
            )
        return JsonResponse({"release": made}, status=201)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        rows = trust.list_releases()
        labels = trust.actor_labels()
    return JsonResponse(
        {
            "releases": [
                {
                    "id": r["public_id"],
                    "version": r["version"],
                    "summary": r["summary"],
                    "details": r["details"],
                    "released_on": r["released_on"],
                    "recorded_by": labels.get(r["recorded_by_ref"], "Zoiko staff"),
                }
                for r in rows
            ]
        }
    )


# ------------------------------------------------------------------ service levels (DEV-130)
def texts_health() -> dict[str, Any]:
    from foundation.texts import circuit_state

    return circuit_state()


@endpoint("GET")
def platform_slo(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """How the service is doing against its two proposed targets, as seen by THIS web process.
    Route patterns and counts only: no tenant, person or address ever appears."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    return JsonResponse(
        {
            "windows": [metrics.slo(5), metrics.slo(60)],
            "routes": metrics.routes_summary(),
            "targets": {
                "availability": metrics.AVAILABILITY_TARGET,
                "latency_seconds": metrics.LATENCY_THRESHOLD,
                "latency_share": metrics.LATENCY_TARGET,
            },
            "texts": texts_health(),
            "limits": [
                "One web process only; a restart resets the numbers.",
                "The longest window is one hour: the 30-day budget needs a metrics store.",
                "Counts requests, not whether a booking or handoff was actually correct.",
            ],
        }
    )


# ------------------------------------------------------------------ fees and invoices (DEV-139)
@endpoint("GET", "POST")
def staff_billing_fees(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: each service and its price today. POST {service, currency, amount_minor, from}."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:billing", organization_public_id=org)
        with tenant_transaction():
            made = billing.set_fee(
                organization_ref=org,
                service_ref=text(body, "service"),
                currency=text(body, "currency"),
                amount_minor=body.get("amount_minor"),
                effective_from=body.get("effective_from"),
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"fee": made}, status=201)
    _authorize(auth, "read:billing", organization_public_id=org)
    with tenant_transaction():
        rows = billing.list_fees(org)
    return JsonResponse({"fees": rows, "can_set": "R13" in auth.roles})


@endpoint("GET", "POST")
def staff_billing_invoices(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: invoices (?status=). POST {person, lines: [{service, quantity}], due_days, booking}."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if request.method == "POST":
        body = json_body(request)
        person = text(body, "person")
        decision = _authorize(auth, "write:billing", organization_public_id=org, subject_ref=person)
        with tenant_transaction():
            made = billing.create_invoice(
                organization_ref=org,
                person_ref=person,
                lines=body.get("lines"),
                due_days=body.get("due_days"),
                booking_ref=text(body, "booking", required=False),
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"invoice": made}, status=201)
    _authorize(auth, "read:billing", organization_public_id=org)
    pg = _page_req(request, auth, f"invoices:{request.GET.get('status') or ''}")
    with tenant_transaction():
        rows = billing.list_invoices(org, request.GET.get("status") or None, page=pg)
    _add_names(auth, rows, "person_ref")
    return JsonResponse(_with_cursor({"invoices": rows, "can_admin": "R13" in auth.roles}, pg))


@endpoint("GET")
def staff_billing_invoice(
    request: HttpRequest, auth: trust.Authenticated, invoice_id: str
) -> HttpResponse:
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:billing", organization_public_id=org)
    with tenant_transaction():
        one = billing.get_invoice(invoice_id, org)
        labels = trust.actor_labels()
    _add_names(auth, [one], "person_ref")
    for h in one["history"]:
        h["actor"] = labels.get(h["actor"], h["actor"])
    return JsonResponse({"invoice": one})


@endpoint("POST")
def staff_billing_action(
    request: HttpRequest, auth: trust.Authenticated, invoice_id: str, action: str
) -> HttpResponse:
    """issue | pay {payment_ref} | void {reason} | credit {amount_minor, reason}."""
    org = _staff_only(auth)
    if org is None or action not in ("issue", "pay", "void", "credit"):
        raise ZoikoError(ErrorCode.NOT_FOUND if org else ErrorCode.POLICY_DENIED)
    body = json_body(request)
    decision = _authorize(auth, "write:billing", organization_public_id=org)
    me = auth.actor_public_id
    with tenant_transaction():
        if action == "credit":
            done = billing.credit_note(
                invoice_ref=invoice_id,
                organization_ref=org,
                amount_minor=body.get("amount_minor"),
                reason=text(body, "reason"),
                actor_public_id=me,
                decision=decision,
            )
        else:
            done = billing.step(
                invoice_ref=invoice_id,
                organization_ref=org,
                action=action,
                payment_ref=text(body, "payment_ref", required=False),
                reason=text(body, "reason", required=False),
                actor_public_id=me,
                decision=decision,
            )
    _add_names(auth, [done], "person_ref")
    return JsonResponse({"invoice": done})


@endpoint("GET")
def my_invoices(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """A patient's own issued, paid and cancelled invoices (never drafts, never a guardian's)."""
    org = _catalog_org(auth)
    with tenant_transaction():
        person = trust.own_person_of(auth.actor_public_id)
    if not person or org is None:
        return JsonResponse({"invoices": []})
    _authorize(auth, "read:billing", organization_public_id=org, subject_ref=person)
    with tenant_transaction():
        rows = billing.patient_invoices(person)
    return JsonResponse({"invoices": rows})


# ------------------------------------------------------------------ requirements matrix (DEV-138)
@endpoint("GET")
def platform_traceability(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        out = traceability.list_requirements(
            request.GET.get("source") or None, request.GET.get("status") or None
        )
    return JsonResponse({**out, "can_write": "R11" in auth.roles})


@endpoint("POST")
def platform_traceability_import(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """{rows: [...]} adds or updates requirements; {seed: true} loads the developer's readings."""
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    rows = traceability.seed_rows() if body.get("seed") is True else body.get("rows")
    with tenant_transaction():
        counts = traceability.import_rows(
            rows=rows, actor_public_id=auth.actor_public_id, decision=decision, organization_ref=org
        )
    return JsonResponse(counts)


@endpoint("POST")
def platform_traceability_update(
    request: HttpRequest, auth: trust.Authenticated, requirement_id: str
) -> HttpResponse:
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    with tenant_transaction():
        done = traceability.update_requirement(
            requirement_ref=requirement_id,
            status=text(body, "status"),
            evidence=text(body, "evidence", required=False),
            actor_public_id=auth.actor_public_id,
            decision=decision,
            organization_ref=org,
        )
    return JsonResponse({"requirement": done})


# ------------------------------------------------------------------ go-live readiness (DEV-137)
def _release_json(r: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    out = dict(r)
    out["author"] = labels.get(r["author"], "Zoiko staff")
    out["requirements"] = [
        {**q, "signer": labels.get(q["signer"], "Zoiko staff") if q["signer"] else ""}
        for q in r["requirements"]
    ]
    return out


@endpoint("GET", "POST")
def platform_readiness(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: releases and where each stands. POST {release_ref, build, cell, country, caps}."""
    org = _platform_only(auth)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:platform", organization_public_id=org)
        with tenant_transaction():
            made = readiness.create_release(
                release_ref=text(body, "release_ref"),
                build=text(body, "build"),
                cell=text(body, "cell"),
                country=text(body, "country"),
                capabilities=body.get("capabilities"),
                actor_public_id=auth.actor_public_id,
                decision=decision,
                organization_ref=org,
            )
            labels = trust.actor_labels()
        return JsonResponse({"release": _release_json(made, labels)}, status=201)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        rows = readiness.list_releases()
        labels = trust.actor_labels()
    return JsonResponse(
        {
            "releases": [_release_json(r, labels) for r in rows],
            "can_write": "R11" in auth.roles,
            "can_attest": "R12" in auth.roles,
        }
    )


@endpoint("GET")
def platform_readiness_one(
    request: HttpRequest, auth: trust.Authenticated, release_id: str
) -> HttpResponse:
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        one = readiness.get_release(release_id)
        labels = trust.actor_labels()
    out = _release_json(one, labels)
    out["history"] = [{**h, "actor": labels.get(h["actor"], "Zoiko staff")} for h in one["history"]]
    return JsonResponse({"release": out})


@endpoint("POST")
def platform_readiness_step(
    request: HttpRequest, auth: trust.Authenticated, release_id: str, step: str
) -> HttpResponse:
    """sign {requirement, reference, expires_on, note} | veto {requirement, note} | evaluate."""
    org = _platform_only(auth)
    if step not in ("sign", "veto", "evaluate"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    body = json_body(request)
    safety = "R12" in auth.roles and "R11" not in auth.roles
    op = "attest:platform" if (step == "sign" or safety) else "write:platform"
    decision = _authorize(auth, op, organization_public_id=org)
    me = auth.actor_public_id
    with tenant_transaction():
        if step == "sign":
            done = readiness.sign(
                release_ref=release_id,
                requirement=text(body, "requirement"),
                reference=text(body, "reference"),
                expires_on=body.get("expires_on"),
                note=text(body, "note", required=False),
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
        elif step == "veto":
            done = readiness.veto(
                release_ref=release_id,
                requirement=text(body, "requirement"),
                note=text(body, "note", required=False),
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
        else:
            done = readiness.re_evaluate(
                release_ref=release_id, actor_public_id=me, decision=decision, organization_ref=org
            )
        labels = trust.actor_labels()
    return JsonResponse({"release": _release_json(done, labels)})


# ------------------------------------------------------------------ country packs (DEV-128)
def _pack_json(p: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    return {
        "id": p["public_id"],
        "pack_ref": p["pack_ref"],
        "country": p["country"],
        "service": p["service"],
        "revision": p["revision"],
        "status": p["status"],
        "manifest": p["manifest"],
        "content_hash": p["content_hash"],
        "note": p["note"],
        "author": labels.get(p["author"], "Zoiko staff"),
        "created_at": p["created_at"],
        "approvals": {
            r: {"by": labels.get(a["by"], "Zoiko staff"), "at": a["at"]}
            for r, a in p["approvals"].items()
        },
        "missing": p["missing"],
        "expired": p["expired"],
        "signature": p["signature"],  # DEV-210: null unless signed; "valid" is checked now
    }


@endpoint("GET", "POST")
def platform_packs(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the country packs. POST {pack_ref, country, service, manifest}: a draft (support)."""
    org = _platform_only(auth)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:platform", organization_public_id=org)
        manifest = body.get("manifest")
        with tenant_transaction():
            made = jurisdiction.create_pack(
                pack_ref=text(body, "pack_ref"),
                country=text(body, "country"),
                service=text(body, "service"),
                manifest=manifest if manifest is not None else {},
                actor_public_id=auth.actor_public_id,
                decision=decision,
                organization_ref=org,
            )
            labels = trust.actor_labels()
        return JsonResponse({"pack": _pack_json(made, labels)}, status=201)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        rows = jurisdiction.list_packs()
        labels = trust.actor_labels()
    return JsonResponse(
        {
            "packs": [_pack_json(r, labels) for r in rows],
            "can_write": "R11" in auth.roles,
            "can_attest": "R12" in auth.roles,
        }
    )


_PACK_STEPS = (
    "edit",
    "validate",
    "submit",
    "reject",
    "approve-legal",
    "approve-clinical",
    "suspend",
    "retire",
    "revalidate",
)


@endpoint("GET")
def platform_pack_preflight(
    request: HttpRequest, auth: trust.Authenticated, pack_id: str
) -> HttpResponse:
    """A dry run: what signing this pack would switch on. It changes nothing (AT-14)."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        return JsonResponse({"preflight": jurisdiction.preflight(pack_id)})


@endpoint("POST")
def platform_pack_step(
    request: HttpRequest, auth: trust.Authenticated, pack_id: str, step: str
) -> HttpResponse:
    org = _platform_only(auth)
    if step not in _PACK_STEPS:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    body = json_body(request)
    safety = "R12" in auth.roles and "R11" not in auth.roles
    attesting = step.startswith("approve-") or (step in ("reject", "suspend") and safety)
    decision = _authorize(
        auth, "attest:platform" if attesting else "write:platform", organization_public_id=org
    )
    me = auth.actor_public_id
    note = text(body, "note", required=False)
    with tenant_transaction():
        if step == "edit":
            done = jurisdiction.edit_pack(
                pack_ref=pack_id,
                manifest=body.get("manifest"),
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
        elif step == "reject":
            done = jurisdiction.reject(
                pack_ref=pack_id,
                note=note,
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
        elif step == "suspend":
            done = jurisdiction.suspend(
                pack_ref=pack_id,
                note=note,
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
        elif step.startswith("approve-"):
            done = jurisdiction.approve(
                pack_ref=pack_id,
                role=step.removeprefix("approve-"),
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
        else:
            action = {
                "validate": jurisdiction.validate,
                "submit": jurisdiction.submit,
                "retire": jurisdiction.retire,
                "revalidate": jurisdiction.revalidate,
            }[step]
            done = action(
                pack_ref=pack_id, actor_public_id=me, decision=decision, organization_ref=org
            )
        labels = trust.actor_labels()
    return JsonResponse({"pack": _pack_json(done, labels)})


def _incident_json(r: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    return {
        "id": r["public_id"],
        "title": r["title"],
        "severity": r["severity"],
        "happened_on": r["happened_on"],
        "details": r["details"],
        "status": r["status"],
        "resolution": r["resolution"],
        "resolved_on": r["resolved_on"],
        "recorded_by": labels.get(r["recorded_by_ref"], "Zoiko staff"),
        "resolved_by": labels.get(r["resolved_by_ref"], "Zoiko staff")
        if r["resolved_by_ref"]
        else "",
        "assessed_by": labels.get(r["assessed_by_ref"], "Zoiko staff")
        if r["assessed_by_ref"]
        else "",
        "assessment_note": r["assessment_note"],
        "safety_impact": r["safety_impact"],
        "handoff_note": r["handoff_note"],
        "handoff_by": labels.get(r["handoff_by_ref"], "Zoiko staff") if r["handoff_by_ref"] else "",
        # DEV-202: who is on call for it, and whether anyone has said "I have it".
        "owner": labels.get(r["owner_ref"], "Zoiko staff") if r["owner_ref"] else "",
        "deputy": labels.get(r["deputy_ref"], "Zoiko staff") if r["deputy_ref"] else "",
        "owner_ref": r["owner_ref"], "deputy_ref": r["deputy_ref"],
        "acknowledged_at": r["acknowledged_at"], "escalated_at": r["escalated_at"],
        "restoration_notice": r["restoration_notice"],
    }


@endpoint("GET", "POST")
def platform_incidents(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The platform's incident log. Support and safety staff may read it; only those who may
    write the platform (support) may add an entry. It must never hold patient details."""
    org = _platform_only(auth)
    if request.method == "POST":
        body = json_body(request)
        try:
            day = dt.date.fromisoformat(text(body, "happened_on"))
        except ValueError:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["happened_on"]) from None
        decision = _authorize(auth, "write:platform", organization_public_id=org)
        with tenant_transaction():
            made = trust.add_incident(
                title=body.get("title"),
                severity=body.get("severity"),
                happened_on=day,
                details=body.get("details", ""),
                safety_impact=body.get("safety_impact", False),
                actor_public_id=auth.actor_public_id,
                decision=decision,
                organization_public_id=org,
            )
            labels = trust.actor_labels()
        return JsonResponse({"incident": _incident_json(made, labels)}, status=201)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        rows = trust.list_incidents()
        labels = trust.actor_labels()
    return JsonResponse({"incidents": [_incident_json(r, labels) for r in rows]})


def _safety_officer(auth: trust.Authenticated) -> str | None:
    """TEMPORARY RULE (DEV-103): hazards are decided only by a safety officer (R12); support (R11)
    may read. The module makes sure whoever assesses a hazard never reviews or accepts it."""
    org = _platform_only(auth)
    if "R12" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return org


@endpoint("GET", "POST")
def platform_purposes(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the intended-purpose statements. POST {capability, statement, classification,
    intended_users, legal_manufacturer}: propose a new version (support or safety staff).
    TEMPORARY RULE (DEV-184): only a safety officer who did not write it may approve."""
    org = _platform_only(auth)
    if request.method == "POST":
        body = json_body(request)
        safety_only = "R12" in auth.roles and "R11" not in auth.roles
        _authorize(
            auth,
            "attest:platform" if safety_only else "write:platform",
            organization_public_id=org,
        )
        capability = text(body, "capability")
        if capability not in jurisdiction.CAPABILITIES:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["capability"])
        with tenant_transaction():
            made = safety.propose(
                capability=capability,
                statement=body.get("statement"),
                classification=body.get("classification"),
                intended_users=body.get("intended_users"),
                legal_manufacturer=body.get("legal_manufacturer", ""),
                actor_ref=auth.actor_public_id,
            )
        return JsonResponse({"purpose": made}, status=201)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        rows = safety.list_purposes()
    return JsonResponse({"purposes": rows, "capabilities": list(jurisdiction.CAPABILITIES)})


@endpoint("POST")
def platform_purpose_step(
    request: HttpRequest, auth: trust.Authenticated, purpose_id: str, step: str
) -> HttpResponse:
    """approve | retire {reason}: safety officers only."""
    _safety_officer(auth)
    if step not in ("approve", "retire"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    body = json_body(request)
    with tenant_transaction():
        if step == "approve":
            made = safety.approve_purpose(purpose_id, actor_ref=auth.actor_public_id)
        else:
            made = safety.retire_purpose(
                purpose_id, actor_ref=auth.actor_public_id, reason=body.get("reason")
            )
    return JsonResponse({"purpose": made})


@endpoint("GET")
def platform_hazards(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The hazard register (support and safety staff read it). Seeded hazards appear on use."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        safety.ensure_seed(auth.actor_public_id)
        rows = safety.list_hazards()
        labels = trust.actor_labels()
    for row in rows:
        for key in ("assessed_by", "reviewer", "owner"):
            row[key] = labels.get(row[key], "") if row[key] else ""
    return JsonResponse(
        {"me": auth.actor_public_id, "can_decide": "R12" in auth.roles, "hazards": rows}
    )


@endpoint("GET")
def platform_hazard_history(
    request: HttpRequest, auth: trust.Authenticated, hazard_id: str
) -> HttpResponse:
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        steps = safety.history(hazard_id)
        labels = trust.actor_labels()
    return JsonResponse({"steps": [{**s, "by": labels.get(s["by"], "")} for s in steps]})


@endpoint("POST")
def platform_hazard_assess(
    request: HttpRequest, auth: trust.Authenticated, hazard_id: str
) -> HttpResponse:
    _safety_officer(auth)
    body = json_body(request)
    with tenant_transaction():
        made = safety.assess(
            hazard_id,
            actor_ref=auth.actor_public_id,
            severity=body.get("severity"),
            probability=body.get("probability"),
            basis_method=body.get("basis_method"),
            residual_risk=body.get("residual_risk"),
            owner_ref=auth.actor_public_id,
            hazardous_situation=body.get("hazardous_situation", ""),
            exposure_path=body.get("exposure_path", ""),
            affected_groups=body.get("affected_groups", ""),
            linked_build=body.get("linked_build", ""),
        )
    return JsonResponse({"hazard": made})


@endpoint("POST")
def platform_hazard_review(
    request: HttpRequest, auth: trust.Authenticated, hazard_id: str
) -> HttpResponse:
    _safety_officer(auth)
    body = json_body(request)
    with tenant_transaction():
        made = safety.review(
            hazard_id,
            actor_ref=auth.actor_public_id,
            verdict=body.get("verdict"),
            note=body.get("note", ""),
        )
    return JsonResponse({"hazard": made})


@endpoint("POST")
def platform_hazard_accept(
    request: HttpRequest, auth: trust.Authenticated, hazard_id: str
) -> HttpResponse:
    _safety_officer(auth)
    body = json_body(request)
    with tenant_transaction():
        made = safety.accept(hazard_id, actor_ref=auth.actor_public_id, note=body.get("note", ""))
    return JsonResponse({"hazard": made})


@endpoint("POST")
def platform_hazard_retire(
    request: HttpRequest, auth: trust.Authenticated, hazard_id: str
) -> HttpResponse:
    _safety_officer(auth)
    body = json_body(request)
    with tenant_transaction():
        made = safety.retire(hazard_id, actor_ref=auth.actor_public_id, reason=body.get("reason"))
    return JsonResponse({"hazard": made})


@endpoint("POST")
def platform_incident_resolve(
    request: HttpRequest, auth: trust.Authenticated, incident_id: str
) -> HttpResponse:
    """Close an open incident with a note on how it ended (support only; once)."""
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    with tenant_transaction():
        made = trust.resolve_incident(
            incident_ref=incident_id,
            resolution=body.get("resolution"),
            actor_public_id=auth.actor_public_id,
            decision=decision,
            organization_public_id=org,
            restoration_notice=body.get("restoration_notice", ""),   # G-10, DEV-202
        )
        labels = trust.actor_labels()
    return JsonResponse({"incident": _incident_json(made, labels)})


@endpoint("POST")
def platform_incident_assess(
    request: HttpRequest, auth: trust.Authenticated, incident_id: str
) -> HttpResponse:
    """TEMPORARY RULE (DEV-176): an incident logged with an unknown severity stays open until a
    safety officer (R12) evaluates it and gives it a level with a note (AT38)."""
    _safety_officer(auth)
    body = json_body(request)
    with tenant_transaction():
        made = trust.assess_incident(
            incident_ref=incident_id,
            severity=body.get("severity"),
            note=body.get("note"),
            actor_public_id=auth.actor_public_id,
        )
        labels = trust.actor_labels()
    return JsonResponse({"incident": _incident_json(made, labels)})


@endpoint("POST")
def platform_incident_handoff(
    request: HttpRequest, auth: trust.Authenticated, incident_id: str
) -> HttpResponse:
    """TEMPORARY RULE (DEV-191): an incident that touches patient safety cannot be closed until a
    safety officer (R12) records the hand-off or fallback that covers it (G-08)."""
    _safety_officer(auth)
    body = json_body(request)
    with tenant_transaction():
        made = trust.record_handoff(
            incident_ref=incident_id, note=body.get("note"), actor_public_id=auth.actor_public_id
        )
        labels = trust.actor_labels()
    return JsonResponse({"incident": _incident_json(made, labels)})


@endpoint("GET")
def platform_incident_on_call_staff(
    request: HttpRequest, auth: trust.Authenticated
) -> HttpResponse:
    """Who may be named owner or deputy: active support and safety staff (DEV-202)."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        labels = trust.actor_labels()
        staff = [
            {"id": ref, "name": labels.get(ref, ref)}
            for ref in sorted(trust.on_call_staff())
        ]
    return JsonResponse({"staff": staff})


@endpoint("GET")
def platform_incident_timeline(
    request: HttpRequest, auth: trust.Authenticated, incident_id: str
) -> HttpResponse:
    """The unchangeable timeline of one incident (DEV-202)."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        entries = trust.incident_timeline(incident_id)
    return JsonResponse({"timeline": [
        {"kind": e["kind"], "by": e["actor_ref"], "note": e["note"], "at": e["at"]}
        for e in entries
    ]})


@endpoint("POST")
def platform_incident_assign(
    request: HttpRequest, auth: trust.Authenticated, incident_id: str
) -> HttpResponse:
    """Name an owner and a different deputy from the on-call staff (DEV-202)."""
    org = _platform_only(auth)
    _authorize(auth, "write:platform", organization_public_id=org)
    body = json_body(request)
    with tenant_transaction():
        made = trust.assign_incident(
            incident_ref=incident_id,
            owner_ref=text(body, "owner"),
            deputy_ref=text(body, "deputy"),
            actor_ref=auth.actor_public_id,
        )
        labels = trust.actor_labels()
    return JsonResponse({"incident": _incident_json(trust.incident_dict(made), labels)})


@endpoint("POST")
def platform_incident_acknowledge(
    request: HttpRequest, auth: trust.Authenticated, incident_id: str
) -> HttpResponse:
    """The named owner or deputy says "I have it" (DEV-202)."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        made = trust.acknowledge_incident(
            incident_ref=incident_id, actor_ref=auth.actor_public_id
        )
        labels = trust.actor_labels()
    return JsonResponse({"incident": _incident_json(trust.incident_dict(made), labels)})


@endpoint("POST")
def platform_customer_sign_up(
    request: HttpRequest, auth: trust.Authenticated, customer_id: str
) -> HttpResponse:
    """Support opens or closes a customer's patient self-sign-up (`enabled`: true or false)."""
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    made = trust.set_customer_sign_up(
        customer_public_id=customer_id,
        enabled=body.get("enabled"),
        actor_public_id=auth.actor_public_id,
        platform_organization_public_id=org,
        decision=decision,
    )
    return JsonResponse({"customer": made})


@endpoint("POST")
def platform_customer_country(
    request: HttpRequest, auth: trust.Authenticated, customer_id: str
) -> HttpResponse:
    """Support records the country a customer operates in (`country`: "GB", or "" to clear it)."""
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    made = trust.set_customer_country(
        customer_public_id=customer_id,
        country=body.get("country"),
        actor_public_id=auth.actor_public_id,
        platform_organization_public_id=org,
        decision=decision,
    )
    return JsonResponse({"customer": made})


def _suspension_json(
    r: dict[str, Any], labels: dict[str, str], names: dict[str, str], me: str
) -> dict[str, Any]:
    return {
        "id": r["public_id"],
        "customer": r["customer_ref"],
        "customer_name": names.get(r["customer_ref"], "A customer"),
        "kind": r["kind"],
        "reason": r["reason"],
        "status": r["status"],
        "requested_by": labels.get(r["requested_by_ref"], "Zoiko staff"),
        "decided_by": labels.get(r["decided_by_ref"], "Zoiko staff") if r["decided_by_ref"] else "",
        "decision_note": r["decision_note"],
        "decided_at": r["decided_at"],
        "created_at": r["created_at"],
        "mine": r["requested_by_ref"] == me,
        "continuity_plan": r["continuity_plan"],
        "open_items": r["open_items"],
    }


def _suspensions_for(auth: trust.Authenticated) -> list[dict[str, Any]]:
    with tenant_transaction():
        rows = trust.list_changes()
        labels = trust.actor_labels()
    names = {t["public_id"]: t["name"] for t in trust.list_tenants()}
    return [_suspension_json(r, labels, names, auth.actor_public_id) for r in rows]


@endpoint("GET")
def platform_suspensions(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Requests to suspend or reinstate customers: waiting ones first, then the newest."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    return JsonResponse({"suspensions": _suspensions_for(auth)})


@endpoint("POST")
def platform_customer_suspension(
    request: HttpRequest, auth: trust.Authenticated, customer_id: str
) -> HttpResponse:
    """Support asks to suspend (or reinstate) a customer. Nothing changes until a DIFFERENT
    support person approves."""
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    open_items = 0
    if body.get("action") == "suspend":
        # Patients still in the middle of something: referrals inside the customer, and partner
        # requests in the platform's shared space (H-05, DEV-193). Read before the request.
        try:
            with tenant_transaction(trust.context_for(customer_id)):
                open_items += referral.referral_counts()["open"]
        except ZoikoError:
            pass  # an unknown customer is refused below
        with tenant_transaction():
            open_items += exchange.open_count_for(customer_id)
    made = trust.request_change(
        customer_public_id=customer_id,
        kind=body.get("action"),
        reason=body.get("reason"),
        actor_public_id=auth.actor_public_id,
        platform_organization_public_id=org,
        decision=decision,
        open_items=open_items,
        continuity_plan=body.get("continuity_plan", ""),
    )
    return JsonResponse({"id": made["public_id"], "status": made["status"]}, status=201)


def _decide_suspension(
    request: HttpRequest, auth: trust.Authenticated, suspension_id: str, approve: bool
) -> HttpResponse:
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    made = trust.decide_change(
        request_ref=suspension_id,
        approve=approve,
        note=body.get("note", ""),
        actor_public_id=auth.actor_public_id,
        platform_organization_public_id=org,
        decision=decision,
    )
    return JsonResponse({"id": made["public_id"], "status": made["status"]})


@endpoint("POST")
def platform_suspension_approve(
    request: HttpRequest, auth: trust.Authenticated, suspension_id: str
) -> HttpResponse:
    return _decide_suspension(request, auth, suspension_id, True)


@endpoint("POST")
def platform_suspension_reject(
    request: HttpRequest, auth: trust.Authenticated, suspension_id: str
) -> HttpResponse:
    return _decide_suspension(request, auth, suspension_id, False)


@endpoint("POST")
def platform_suspension_cancel(
    request: HttpRequest, auth: trust.Authenticated, suspension_id: str
) -> HttpResponse:
    org = _platform_only(auth)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    made = trust.cancel_change(
        request_ref=suspension_id,
        actor_public_id=auth.actor_public_id,
        platform_organization_public_id=org,
        decision=decision,
    )
    return JsonResponse({"id": made["public_id"], "status": made["status"]})


@endpoint("POST")
def platform_customers(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Zoiko support adds a customer: its first site and an administrator who is emailed an
    invitation to choose their own password. Needs the right to WRITE the platform (R11)."""
    org = _platform_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    made = trust.onboard_customer(
        customer_name=text(body, "customer"),
        site_name=text(body, "site"),
        admin_email=text(body, "admin_email"),
        actor_public_id=auth.actor_public_id,
        platform_organization_public_id=org,
        decision=decision,
        kind=text(body, "kind", required=False) or "provider",
    )
    return JsonResponse({"customer": made}, status=201)


# -------------------------------------------------------------- partner, payer, employer portal
PORTAL_AREAS = ("partner", "payer", "employer", "developer")

# What is really connected for each kind of login today, said plainly so a screen never implies
# more (TEMPORARY, DEV-158): the rest waits on decisions in docs/LEAD-NOTE.md.
_PORTAL_CONNECTED = {
    "partner": [
        "Your organisation's service listings (add, request a listing, a colleague reviews it)",
        "Your team (add colleagues of your own kind)",
    ],
    "payer": ["Plans, members and entitlement checks", "Your team (add colleagues)"],
    "employer": ["Programme counts (small groups hidden)", "Your team (add colleagues)"],
    "developer": [
        "Your API keys, webhooks and delivery log",
        "The API and event documents",
        "Your team (add colleagues)",
    ],
}
_PORTAL_WAITING = {
    "developer": [
        "A sandbox with synthetic patients, partner certification and onboarding "
        "checks (GHE-13): not built. Nothing here reaches production patient data."
    ],
    "partner": [
        "Receiving and answering referrals, orders and results: this needs a way for "
        "separate customers to exchange them, which is not built."
    ],
    "payer": [
        "Claims, and a clinic asking you whether a patient is covered: this needs a way "
        "for separate customers to exchange them, which is not built (GHE-17)."
    ],
    "employer": [
        "Counts about how your people use care (visits, bookings): this needs a link "
        "between your programme and clinics, which is not built. You see counts only, "
        "never an individual (GHE-17)."
    ],
}


@endpoint("GET")
def portal_overview(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """For a partner, payer or employer login: who you are here, what your role may reach (from
    the same rules the server enforces) and what is and is not connected yet."""
    area = home_for(auth.actor_type, list(auth.roles), auth.tenant_type)
    if area not in PORTAL_AREAS:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with tenant_transaction():
        held = trust.active_roles(auth.actor_public_id)
        orgs = trust.organization_names()
    roles = []
    for code, org in held:
        info = trust.role_info(code)
        if info is not None:
            roles.append(
                {
                    **info,
                    "organization": {"id": org, "name": orgs.get(org, "")},
                    "can": policy.role_capabilities(code),
                }
            )
    return JsonResponse(
        {
            "area": area,
            "tenant": auth.tenant_name,
            "roles": roles,
            "connected": _PORTAL_CONNECTED[area],
            "waiting": _PORTAL_WAITING[area],
        }
    )


@endpoint("GET")
def portal_dashboard(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The numbers on a partner, payer, employer or developer's home screen. Numbers only."""
    org = _portal_org(auth, *PORTAL_AREAS)
    area = home_for(auth.actor_type, list(auth.roles), auth.tenant_type)
    me = auth.actor_public_id
    with tenant_transaction():
        team = sum(
            1
            for m in trust.staff_directory()["staff"]
            if m["status"] == "active" and any(r["organization_id"] == org for r in m["roles"])
        )
    out: dict[str, Any] = {"area": area, "team": team}
    if area == "partner":
        _authorize(auth, "read:catalog", organization_public_id=org)
        with tenant_transaction():
            rows = [
                s for s in catalog.list_services(active_only=False) if s["organization_ref"] == org
            ]
        state = lambda s: s["listing_state"]  # noqa: E731
        out["services"] = {
            "total": len(rows),
            "listed": sum(state(s) == "published" for s in rows),
            "waiting_review": sum(state(s) == "evidence_pending" for s in rows),
            "not_listed": sum(state(s) == "draft" for s in rows),
        }
        out["to_review"] = sum(
            state(s) == "evidence_pending" and s["requested_by_ref"] != me for s in rows
        )
        out["requests"] = {"new": 0, "in_progress": 0, "delivered": 0}
        if any(t["type"] == "platform" and t["status"] == "active" for t in trust.list_tenants()):
            with tenant_transaction(_platform_context()):
                out["requests"] = exchange.partner_counts(_my_customer(auth))
    elif area == "payer":
        decision = _authorize(auth, "read:coverage", organization_public_id=org)
        with tenant_transaction():
            out["plans"] = sponsor.payer_summary(
                organization_ref=org, actor_public_id=me, decision=decision
            )
    elif area == "employer":
        decision = _authorize(auth, "read:program", organization_public_id=org)
        with tenant_transaction():
            out["programme"] = sponsor.program_summary(
                organization_ref=org, actor_public_id=me, decision=decision
            )
    else:
        _authorize(auth, "read:integration", organization_public_id=org)
        with tenant_transaction():
            keys = integration.list_clients()
            hooks = integration.list_endpoints()
            recent = integration.list_deliveries(None)
            gave_up = integration.gave_up_count()
        out["keys"] = {"active": sum(k["state"] == "active" for k in keys), "total": len(keys)}
        out["webhooks"] = {
            "active": sum(h["state"] == "active" for h in hooks),
            "total": len(hooks),
        }
        out["deliveries"] = {
            "recent": len(recent),
            "delivered": sum(d["state"] == "delivered" for d in recent),
            "retrying": sum(d["state"] in ("pending", "retrying") for d in recent),
            "gave_up": gave_up,
        }
    return JsonResponse(out)


def _portal_org(auth: trust.Authenticated, *areas: str) -> str:
    """The organisation a partner, payer or employer login works for; refuses any other kind."""
    area = home_for(auth.actor_type, list(auth.roles), auth.tenant_type)
    org = _catalog_org(auth)
    if area not in areas or org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return org


@endpoint("GET", "POST")
def portal_team(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the people in this organisation. POST {email}: add a colleague of your own kind."""
    org = _portal_org(auth, *PORTAL_AREAS)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:staff", organization_public_id=org)
        with tenant_transaction():
            member = trust.invite_colleague(
                email=text(body, "email"),
                organization_public_id=org,
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"member": member}, status=201)
    _authorize(auth, "read:staff", organization_public_id=org)
    with tenant_transaction():
        listing = trust.staff_directory()
    people = [
        {
            "id": m["id"],
            "login": m["login"],
            "status": m["status"],
            "pending_invite": m["pending_invite"],
            "you": m["id"] == auth.actor_public_id,
            "roles": [r["role"] for r in m["roles"]],
        }
        for m in listing["staff"]
        if any(r["organization_id"] == org for r in m["roles"])
    ]
    return JsonResponse({"team": people})


@endpoint("POST")
def portal_team_member(
    request: HttpRequest, auth: trust.Authenticated, member_id: str, step: str
) -> HttpResponse:
    """suspend | reinstate | resend: for someone in your own organisation, never yourself."""
    org = _portal_org(auth, *PORTAL_AREAS)
    if step not in ("suspend", "reinstate", "resend"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    decision = _authorize(auth, "write:staff", organization_public_id=org)
    with tenant_transaction():
        if step == "resend":
            trust.resend_invitation(
                member_id,
                organization_public_id=org,
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
            return JsonResponse({"sent": True}, status=202)
        done = trust.set_member_status(
            member_id,
            "suspended" if step == "suspend" else "active",
            organization_public_id=org,
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"member": done})


@endpoint("GET")
def portal_program(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The employer's programme: counts only, small groups hidden."""
    org = _portal_org(auth, "employer")
    decision = _authorize(auth, "read:program", organization_public_id=org)
    with tenant_transaction():
        return JsonResponse(
            sponsor.program_summary(
                organization_ref=org, actor_public_id=auth.actor_public_id, decision=decision
            )
        )


@endpoint("POST")
def portal_program_members(
    request: HttpRequest, auth: trust.Authenticated, step: str
) -> HttpResponse:
    """enrol | withdraw {refs: [...]}: opaque references, never names."""
    org = _portal_org(auth, "employer")
    if step not in ("enrol", "withdraw"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    body = json_body(request)
    decision = _authorize(auth, "write:program", organization_public_id=org)
    call = sponsor.add_members if step == "enrol" else sponsor.withdraw_members
    with tenant_transaction():
        done = call(
            organization_ref=org,
            refs=body.get("refs"),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse(done)


@endpoint("GET", "POST")
def portal_plans(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The payer's plans. POST {code, name, covered_categories, copay_percent}."""
    org = _portal_org(auth, "payer")
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:coverage", organization_public_id=org)
        with tenant_transaction():
            made = sponsor.create_plan(
                organization_ref=org,
                code=body.get("code"),
                name=body.get("name"),
                covered_categories=body.get("covered_categories"),
                copay_percent=body.get("copay_percent"),
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"plan": made}, status=201)
    decision = _authorize(auth, "read:coverage", organization_public_id=org)
    with tenant_transaction():
        rows = sponsor.list_plans(
            organization_ref=org, actor_public_id=auth.actor_public_id, decision=decision
        )
    return JsonResponse({"plans": rows})


@endpoint("POST")
def portal_plan_update(
    request: HttpRequest, auth: trust.Authenticated, plan_id: str
) -> HttpResponse:
    org = _portal_org(auth, "payer")
    body = json_body(request)
    decision = _authorize(auth, "write:coverage", organization_public_id=org)
    with tenant_transaction():
        done = sponsor.update_plan(
            plan_ref=plan_id,
            organization_ref=org,
            covered_categories=body.get("covered_categories"),
            copay_percent=body.get("copay_percent"),
            active=body.get("active"),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"plan": done})


@endpoint("GET", "POST")
def portal_members(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The payer's members, numbers shown masked.
    POST {plan, member_id, valid_from?, valid_until?}."""
    org = _portal_org(auth, "payer")
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:coverage", organization_public_id=org)
        with tenant_transaction():
            made = sponsor.add_plan_member(
                organization_ref=org,
                plan_code=body.get("plan"),
                member_id=body.get("member_id"),
                valid_from=body.get("valid_from"),
                valid_until=body.get("valid_until"),
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"member": made}, status=201)
    decision = _authorize(auth, "read:coverage", organization_public_id=org)
    with tenant_transaction():
        rows = sponsor.list_plan_members(
            organization_ref=org, actor_public_id=auth.actor_public_id, decision=decision
        )
    return JsonResponse({"members": rows})


@endpoint("POST")
def portal_member_end(
    request: HttpRequest, auth: trust.Authenticated, member_id: str
) -> HttpResponse:
    org = _portal_org(auth, "payer")
    decision = _authorize(auth, "write:coverage", organization_public_id=org)
    with tenant_transaction():
        done = sponsor.end_plan_member(
            member_ref=member_id,
            organization_ref=org,
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"member": done})


@endpoint("POST")
def portal_entitlement(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Is this member covered for this kind of service? {member_id, category, on?}."""
    org = _portal_org(auth, "payer")
    body = json_body(request)
    decision = _authorize(auth, "read:coverage", organization_public_id=org)
    with tenant_transaction():
        answer = sponsor.check_entitlement(
            organization_ref=org,
            member_id=body.get("member_id"),
            category=body.get("category"),
            on=body.get("on"),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse(answer)


# ------------------------------------------------------------------ the exchange with partners
def _page_req(request: HttpRequest, auth: trust.Authenticated, lst: str) -> Any:
    """The page asked for (`?limit=` and/or `?cursor=`), or None for the whole list as before
    (DEV-337). The cursor is tied to this login and this list."""
    if "limit" not in request.GET and "cursor" not in request.GET:
        return None
    return pagination.PageRequest(
        limit=pagination.limit_from(request.GET.get("limit")),
        cursor=request.GET.get("cursor") or None, actor=auth.actor_public_id, lst=lst)


def _with_cursor(body: dict[str, Any], pg: Any) -> dict[str, Any]:
    if pg is not None:
        body["next_cursor"] = pg.next_cursor
    return body


def _platform_context() -> Any:
    """The exchange lives in Zoiko's own platform space; this is the context to reach it, always
    used OUTSIDE the caller's own transaction (switching tenants inside one would lose it)."""
    platform = next(
        (t for t in trust.list_tenants() if t["type"] == "platform" and t["status"] == "active"),
        None,
    )
    if platform is None:
        raise ZoikoError(ErrorCode.SERVICE_UNAVAILABLE)
    return trust.context_for(platform["public_id"])


def _my_customer(auth: trust.Authenticated) -> str:
    return trust.tenant_public_id(auth.context.tenant_id)


def _clinic_org(auth: trust.Authenticated) -> str:
    """Clinic staff or a doctor, working for a clinic."""
    org = _catalog_org(auth)
    if not _clinic_side(auth) or org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return org


def _partner_tenants() -> list[dict[str, str]]:
    return [t for t in trust.list_tenants() if t["type"] == "partner" and t["status"] == "active"]


@endpoint("GET")
def exchange_partners(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The partners a clinic may send a request to (name and id only)."""
    _clinic_org(auth)
    return JsonResponse(
        {"partners": [{"id": t["public_id"], "name": t["name"]} for t in _partner_tenants()]}
    )


@endpoint("GET", "POST")
def exchange_requests(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: requests this clinic sent (a doctor: only for their own patients).
    POST {partner, kind, label, token, urgency, patient}: send one. References and status only."""
    org = _clinic_org(auth)
    mine = _my_customer(auth)
    if request.method == "POST":
        body = json_body(request)
        person = text(body, "patient")
        _authorize(auth, "write:referral", organization_public_id=org, subject_ref=person)
        target = next(
            (t for t in _partner_tenants() if t["public_id"] == body.get("partner")), None
        )
        if target is None:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["partner"])
        with tenant_transaction(_platform_context()):
            made = exchange.send(
                from_tenant_ref=mine,
                from_name=auth.tenant_name,
                to_tenant_ref=target["public_id"],
                to_name=target["name"],
                kind=body.get("kind"),
                subject_token=body.get("token"),
                person_ref=person,
                label=body.get("label"),
                urgency=body.get("urgency") or "routine",
                actor_ref=auth.actor_public_id,
                order_code=body.get("order_code") or "",
            )
        with tenant_transaction():
            evidence.record(
                actor_ref=auth.actor_public_id,
                action="exchange.send",
                object_type="exchange_request",
                object_ref=made["id"],
                outcome=evidence.AuditOutcome.ALLOWED,
            )
            exchange.announce_consent_needed(
                request_ref=made["id"], person_ref=person, kind=made["kind"]
            )
        out = [made]
        _add_names(auth, out, "patient")  # runs a policy check: outside a transaction
        return JsonResponse({"request": out[0]}, status=201)
    if not _is_doctor(auth):
        _authorize(auth, "read:referral", organization_public_id=org)
    persons = None
    if _is_doctor(auth):
        with tenant_transaction():
            mine_patients = sorted(
                {c["person_ref"] for c in trust.care_patients(auth.actor_public_id)}
            )
        persons = [
            p
            for p in mine_patients
            if is_allowed(  # outside a transaction
                auth, "read:referral", organization_public_id=org, subject_ref=p
            )
        ]
    paged = "limit" in request.GET or "cursor" in request.GET
    next_cursor: str | None = None
    with tenant_transaction(_platform_context()):
        if paged:
            rows, next_cursor = exchange.page_sent(
                tenant_ref=mine,
                person_refs=persons,
                state=request.GET.get("state") or None,
                limit=pagination.limit_from(request.GET.get("limit")),
                cursor=request.GET.get("cursor") or None,
                actor_ref=auth.actor_public_id,
            )
        else:
            rows = exchange.list_sent(
                tenant_ref=mine, person_refs=persons, state=request.GET.get("state") or None
            )
    _add_names(auth, rows, "patient")
    with tenant_transaction():
        labels = trust.actor_labels()
    checking = identity_held([r["patient"] for r in rows])     # DEV-385
    for row in rows:
        row["identity_check_open"] = row["patient"] in checking
        row["hold_owner_name"] = labels.get(row["hold_owner"], "") if row["hold_owner"] else ""
        row["hold_mine"] = bool(row["hold_owner"]) and row["hold_owner"] == auth.actor_public_id
    return JsonResponse({"requests": rows, **({"next_cursor": next_cursor} if paged else {})})


def _is_doctor(auth: trust.Authenticated) -> bool:
    return home_for(auth.actor_type, list(auth.roles), auth.tenant_type) == "clinician"


@endpoint("POST")
def exchange_request_step(
    request: HttpRequest, auth: trust.Authenticated, request_id: str, step: str
) -> HttpResponse:
    """cancel | review. Reviewing is a clinician's own step: delivery is never review."""
    org = _clinic_org(auth)
    mine = _my_customer(auth)
    if step not in ("cancel", "review"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if step == "review" and "R03" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with tenant_transaction(_platform_context()):
        person = exchange.person_of(request_id, mine)
    _authorize(
        auth,
        "attest:referral" if step == "review" else "write:referral",
        organization_public_id=org,
        subject_ref=person,
    )
    version: int | None = None
    if step == "review":
        # Never reviewed while it may belong to the other record of a duplicate pair (DEV-385).
        refuse_if_identity_held(person)
        # The version of the result the doctor read (DEV-373): a review of anything else is refused.
        sent = json_body(request).get("version")
        if isinstance(sent, bool) or not isinstance(sent, int) or sent < 1:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["version"])
        version = sent
    with tenant_transaction(_platform_context()):
        done = exchange.clinic_step(
            request_ref=request_id, tenant_ref=mine, step=step, actor_ref=auth.actor_public_id,
            version=version,
        )
    changed = done.pop("changed", False)
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action=f"exchange.{step}",
            object_type="exchange_request",
            object_ref=request_id,
            outcome=evidence.AuditOutcome.ALLOWED,
        )
        if step == "review" and changed:  # tell the patient once, in the clinic's own space
            exchange.announce_reviewed(request_ref=request_id, person_ref=person, kind=done["kind"])
    out = [done]
    _add_names(auth, out, "patient")
    return JsonResponse({"request": out[0]})


@endpoint("GET")
def exchange_history(
    request: HttpRequest, auth: trust.Authenticated, request_id: str
) -> HttpResponse:
    """The steps of one request, for the clinic that sent it or the partner it went to."""
    org = _catalog_org(auth)
    area = home_for(auth.actor_type, list(auth.roles), auth.tenant_type)
    if org is None or area not in ("operations", "clinician", "partner"):
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with tenant_transaction(_platform_context()):
        steps = exchange.history(request_ref=request_id, tenant_ref=_my_customer(auth))
    return JsonResponse({"history": steps})


# What a patient may read, by the kind of request (the role table decides, per patient).
_EXCHANGE_READ = {
    "lab_order": "read:diagnostic",
    "pharmacy_request": "read:medication",
    "homecare_referral": "read:referral",
}


@endpoint("GET")
def exchange_mine(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The patient's (or guardian's) own requests: what was asked of whom and how far it has got.
    Shown only for people they may read; never an order number, a reference or a reason."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    mine = _my_customer(auth)
    allowed: dict[tuple[str, str], bool] = {}
    pg = _page_req(request, auth, "exchange_mine")
    with tenant_transaction(_platform_context()):
        rows = exchange.list_for_patients(tenant_ref=mine, person_refs=reachable, page=pg)
    # Held while the patient is in an open duplicate-record case (DEV-385): no values shown.
    checking = identity_held([r["person"] for r in rows])
    for r in rows:
        if r["person"] in checking:
            r["values_ready"] = False
            r["held"] = r.get("held") or IDENTITY_CHECK
    out = []
    for r in rows:
        key = (r["kind"], r["person"])
        if key not in allowed:
            allowed[key] = is_allowed(auth, _EXCHANGE_READ[r["kind"]], subject_ref=r["person"])
        if allowed[key]:
            out.append({k: v for k, v in r.items() if k != "person"})
    return JsonResponse(_with_cursor({"requests": out}, pg))


@endpoint("GET")
def exchange_mine_values(
    request: HttpRequest, auth: trust.Authenticated, request_id: str
) -> HttpResponse:
    """The measured values of one of the patient's own results, a day after a doctor reviewed it
    (DEV-332). Only the patient themselves (a guardian holds no "records" part, DEV-110)."""
    mine = _my_customer(auth)
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    with tenant_transaction(_platform_context()):
        rows = exchange.list_for_patients(tenant_ref=mine, person_refs=reachable)
    found = next((r for r in rows if r["id"] == request_id), None)
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if not is_allowed(auth, _EXCHANGE_READ[found["kind"]], subject_ref=found["person"]):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if identity_held([found["person"]]):        # held during a duplicate-record check (DEV-385)
        raise ZoikoError(ErrorCode.NOT_FOUND)
    with tenant_transaction(_platform_context()):
        got = exchange.patient_values(
            request_ref=request_id, tenant_ref=mine, person_refs=[found["person"]],
            actor_ref=auth.actor_public_id,
        )
    return JsonResponse({"result": got})


@endpoint("GET")
def exchange_mine_trend(
    request: HttpRequest, auth: trust.Authenticated, request_id: str
) -> HttpResponse:
    """The historical trend of one measured value across this patient's reviewed results, a day
    after a doctor reviewed them (DEV-372). Only the patient themselves (or an authorized guardian
    with records access)."""
    mine = _my_customer(auth)
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    with tenant_transaction(_platform_context()):
        rows = exchange.list_for_patients(tenant_ref=mine, person_refs=reachable)
    found = next((r for r in rows if r["id"] == request_id), None)
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if not is_allowed(auth, _EXCHANGE_READ[found["kind"]], subject_ref=found["person"]):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if not found["values_ready"] or identity_held([found["person"]]):
        # Asked from a result the patient may not see yet (as for its own values), or one held
        # during a duplicate-record check (DEV-385): not found.
        raise ZoikoError(ErrorCode.NOT_FOUND)
    key = request.GET.get("key")
    with tenant_transaction(_platform_context()):
        points = exchange.patient_value_trend(
            person_ref=found["person"], tenant_ref=mine, key=key,
            actor_ref=auth.actor_public_id,
        )
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id, action="exchange.patient_trend_read",
            object_type="exchange_request", object_ref=request_id,
            outcome=evidence.AuditOutcome.ALLOWED,
        )
    return JsonResponse({"points": points})



@endpoint("POST")
def exchange_mine_step(
    request: HttpRequest, auth: trust.Authenticated, request_id: str, step: str
) -> HttpResponse:
    """The patient (or a guardian) answers a request made for them: agree (to tell the partner
    their name and birth date, for this order only), decline, or withdraw an agreement."""
    if step not in ("agree", "decline", "withdraw"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    mine = _my_customer(auth)
    with tenant_transaction():
        reachable = set(trust.reachable_persons(auth.actor_public_id))
    with tenant_transaction(_platform_context()):
        person = exchange.person_of(request_id, mine)
        kind = exchange.sent_kind_of(request_id, mine)
    if person not in reachable:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    decision = _authorize(auth, _EXCHANGE_READ[kind], subject_ref=person)
    profile: dict[str, str] = {}
    if step == "agree":
        with tenant_transaction():
            profile = trust.get_person_profiles([person]).get(person, {})
    with tenant_transaction(_platform_context()):
        if step == "agree":
            done = exchange.grant_consent(
                request_ref=request_id,
                tenant_ref=mine,
                actor_ref=auth.actor_public_id,
                name=profile.get("name", ""),
                birth_date=profile.get("birth_date", ""),
            )
        elif step == "decline":
            done = exchange.decline_consent(
                request_ref=request_id, tenant_ref=mine, actor_ref=auth.actor_public_id
            )
        else:
            done = exchange.withdraw_consent(
                request_ref=request_id, tenant_ref=mine, actor_ref=auth.actor_public_id
            )
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action=f"exchange.consent_{step}",
            object_type="exchange_request",
            object_ref=request_id,
            outcome=evidence.AuditOutcome.ALLOWED,
        )
    if step == "agree":
        _send_order(auth, request_id, mine, person, _EXCHANGE_READ[kind], decision)
    return JsonResponse({"request": {k: v for k, v in done.items() if k != "person"}})


def _send_order(
    auth: trust.Authenticated, request_id: str, clinic_ref: str, person: str, operation: str,
    decision: policy.Decision,
) -> None:
    """The patient's agreement sends a coded order to the lab's own system, if it has chosen one
    that Zoiko has put live (DEV-323, FHIR step 3). The patient's own "I agree" is the authority:
    it is the receipt, so "who asked" stays true in the audit. Asking twice sends once (the key is
    per request and agreement). A failure never undoes the agreement: the lab still sees the order
    in the partner app."""
    from modules.connector import api as connector

    try:
        with tenant_transaction(_platform_context()):
            order = exchange.order_to_send(request_ref=request_id, tenant_ref=clinic_ref)
        if order is None:
            return
        # The lab's choice across ALL its organisations (DEV-341): the database gives them in no
        # fixed order, so "the first one" sent an order only some of the time. Exactly one
        # chosen system sends; two different choices are not guessed between.
        with tenant_transaction(trust.context_for(order["partner"])):
            chosen = {trust.booking_system_of(o) for o in trust.organization_names()} - {""}
        if len(chosen) != 1:
            if len(chosen) > 1:
                logging.getLogger("zoiko.orders").warning("lab has several order systems")
            return
        system = chosen.pop()
        if not connector.is_live(system):
            return
        with tenant_transaction():
            clinic_orgs = sorted(trust.organization_names())
            connector.request_operation(
                connector_key=system, operation_type="exchange.order", subject_ref=request_id,
                organization_ref=clinic_orgs[0] if clinic_orgs else "",
                idempotency_key=order["key"], request=order["payload"],
                requested_by_ref=auth.actor_public_id, decision=decision,
                authorized_as=operation, receipt_subject_ref=person,
                receipt_without_organization=True,
            )
    except Exception as exc:  # noqa: BLE001 - the agreement stands; the lab still has the app
        logging.getLogger("zoiko.orders").warning("order send failed: %s", type(exc).__name__)


# ------------------------------------------------------------------ the lab's PDF (DEV-345)
@endpoint("POST")
def exchange_incoming_attachment(
    request: HttpRequest, auth: trust.Authenticated, request_id: str
) -> HttpResponse:
    """The lab attaches its PDF report to a delivered result: the raw file as the request body,
    `?filename=`. At most 5 MB; read here with that cap, not Django's general one."""
    org = _portal_org(auth, "partner")
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        kind = exchange.kind_of(request_id, mine)
    if exchange.role_for(kind) not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "write:partner", organization_public_id=org)
    data = request.read(5 * 1024 * 1024 + 1)
    with tenant_transaction(_platform_context()):
        done = exchange.attach_pdf(request_ref=request_id, tenant_ref=mine,
                                   actor_ref=auth.actor_public_id,
                                   filename=request.GET.get("filename"), data=data)
    return JsonResponse({"attachment": done}, status=201)


@endpoint("GET")
def exchange_request_attachments(
    request: HttpRequest, auth: trust.Authenticated, request_id: str
) -> HttpResponse:
    """The files on a result (names, sizes, scan state), for the ordering clinic's treating
    doctor or the lab that sent them."""
    mine = _my_customer(auth)
    if home_for(auth.actor_type, list(auth.roles), auth.tenant_type) == "partner":
        org = _portal_org(auth, "partner")
        _authorize(auth, "read:partner", organization_public_id=org)
    else:
        org = _clinic_org(auth)
        _clinician_only(auth)
        with tenant_transaction(_platform_context()):
            person = exchange.person_of(request_id, mine)
        _authorize(auth, "read:diagnostic", organization_public_id=org, subject_ref=person)
    with tenant_transaction(_platform_context()):
        rows = exchange.list_attachments(request_ref=request_id, tenant_ref=mine)
    return JsonResponse({"attachments": rows})


@endpoint("GET")
def exchange_request_attachment_file(
    request: HttpRequest, auth: trust.Authenticated, request_id: str, attachment_id: str
) -> HttpResponse:
    """The PDF itself, for the ordering clinic's treating doctor only, and only once scanned
    clean. Recorded. Sent as a download, never shown inside the app."""
    org = _clinic_org(auth)
    _clinician_only(auth)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        person = exchange.person_of(request_id, mine)
    _authorize(auth, "read:diagnostic", organization_public_id=org, subject_ref=person)
    with tenant_transaction(_platform_context()):
        name, data = exchange.open_attachment(
            request_ref=request_id, attachment_ref=attachment_id, tenant_ref=mine,
            actor_ref=auth.actor_public_id)
    with tenant_transaction():
        evidence.record(actor_ref=auth.actor_public_id, action="exchange.attachment_opened",
                        object_type="exchange_request", object_ref=request_id,
                        outcome=evidence.AuditOutcome.ALLOWED)
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@endpoint("GET")
def exchange_request_trend(
    request: HttpRequest, auth: trust.Authenticated, request_id: str
) -> HttpResponse:
    """?key=<SNOMED code or value name>: that value across this patient's results at the clinic,
    oldest first (DEV-339). The same rule as reading a result: a treating doctor only."""
    org = _clinic_org(auth)
    _clinician_only(auth)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        person = exchange.person_of(request_id, mine)
    _authorize(auth, "read:diagnostic", organization_public_id=org, subject_ref=person)
    with tenant_transaction(_platform_context()):
        points = exchange.value_trend(
            person_ref=person, tenant_ref=mine, key=request.GET.get("key"),
            actor_ref=auth.actor_public_id)
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id, action="exchange.trend_read",
            object_type="exchange_request", object_ref=request_id,
            outcome=evidence.AuditOutcome.ALLOWED)
    return JsonResponse({"points": points})


@endpoint("GET")
def exchange_request_result(
    request: HttpRequest, auth: trust.Authenticated, request_id: str
) -> HttpResponse:
    """The lab's summary of a delivered result and its rating (normal, abnormal, critical). A
    treating doctor of that patient only: not clinic staff, not the patient. Every read is
    recorded."""
    org = _clinic_org(auth)
    _clinician_only(auth)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        person = exchange.person_of(request_id, mine)
    _authorize(auth, "read:diagnostic", organization_public_id=org, subject_ref=person)
    with tenant_transaction(_platform_context()):
        got = exchange.read_result(
            request_ref=request_id, tenant_ref=mine, actor_ref=auth.actor_public_id
        )
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action="exchange.result_read",
            object_type="exchange_request",
            object_ref=request_id,
            outcome=evidence.AuditOutcome.ALLOWED,
        )
    return JsonResponse({"result": got})


# -------------------------------------------------------- employer programme links (DEV-312)
@endpoint("GET", "POST")
def programme_codes(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: where each member's code stands (no codes, no people). POST {member_ref}: make a
    one-time code for an enrolled member; it is shown once."""
    org = _portal_org(auth, "employer")
    mine = _my_customer(auth)
    if request.method == "POST":
        body = json_body(request)
        _authorize(auth, "write:program", organization_public_id=org)
        member = text(body, "member_ref")
        with tenant_transaction():
            enrolled = sponsor.member_active(organization_ref=org, member_ref=member)
        if not enrolled:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["member_ref"])
        with tenant_transaction(_platform_context()):
            made = programme.issue(
                employer_tenant_ref=mine, employer_name=auth.tenant_name, member_ref=member,
                actor_ref=auth.actor_public_id,
            )
        with tenant_transaction():
            evidence.record(
                actor_ref=auth.actor_public_id,
                action="programme.code_issued",
                object_type="programme_link",
                object_ref=made["id"],
                outcome=evidence.AuditOutcome.ALLOWED,
            )
        return JsonResponse({"code": made}, status=201)
    _authorize(auth, "read:program", organization_public_id=org)
    paged = "limit" in request.GET or "cursor" in request.GET
    next_cursor: str | None = None
    with tenant_transaction(_platform_context()):
        if paged:
            rows, next_cursor = programme.page_for_employer(
                employer_tenant_ref=mine,
                limit=pagination.limit_from(request.GET.get("limit")),
                cursor=request.GET.get("cursor") or None,
                actor_ref=auth.actor_public_id,
            )
        else:
            rows = programme.list_for_employer(employer_tenant_ref=mine)
    return JsonResponse({"codes": rows, **({"next_cursor": next_cursor} if paged else {})})


@endpoint("POST")
def programme_code_revoke(
    request: HttpRequest, auth: trust.Authenticated, member_ref: str
) -> HttpResponse:
    """Cancel an unused code, or end a link. That person stops being counted at once."""
    org = _portal_org(auth, "employer")
    _authorize(auth, "write:program", organization_public_id=org)
    with tenant_transaction(_platform_context()):
        done = programme.revoke(
            employer_tenant_ref=_my_customer(auth), member_ref=member_ref,
            actor_ref=auth.actor_public_id,
        )
    return JsonResponse({"code": done})


@endpoint("GET")
def programme_activity(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Appointments and visits by month for the people linked to this programme: totals only. A
    month is shown only when at least the minimum group of DIFFERENT people contributed (10 by
    default); otherwise it is hidden, never shown as a small number. Never a person, a service or
    a clinic."""
    org = _portal_org(auth, "employer")
    _authorize(auth, "read:program", organization_public_id=org)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        linked = programme.linked_by_clinic(employer_tenant_ref=mine)
    months: dict[str, dict[str, Any]] = {}
    people: set[tuple[str, str]] = set()
    for clinic_ref, persons in linked.items():
        people |= {(clinic_ref, p) for p in persons}
        with tenant_transaction(trust.context_for(clinic_ref)):
            activity = scheduling.monthly_activity(persons)
        for month, per_person in activity.items():
            row = months.setdefault(month, {"who": set(), "bookings": 0, "visits": 0})
            for person, (bookings, visits) in per_person.items():
                row["who"].add((clinic_ref, person))
                row["bookings"] += bookings
                row["visits"] += visits
    k = sponsor.min_group()
    shown = [
        {
            "month": month,
            "bookings": row["bookings"] if len(row["who"]) >= k else None,
            "visits": row["visits"] if len(row["who"]) >= k else None,
            "hidden": len(row["who"]) < k,
        }
        for month, row in sorted(months.items(), reverse=True)[:12]
    ]
    return JsonResponse(
        {
            "min_group": k,
            "linked": len(people) if len(people) >= k else None,
            "linked_hidden": 0 < len(people) < k,
            "months": shown,
        }
    )


@endpoint("GET")
def programme_mine(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The employer programmes this patient is linked to."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        rows = programme.list_for_patients(clinic_tenant_ref=mine, person_refs=reachable)
    shown = []
    for r in rows:
        if is_allowed(auth, "read:person", subject_ref=r["person"]):
            shown.append({k: v for k, v in r.items() if k != "person"})
    return JsonResponse({"programmes": shown})


@endpoint("POST")
def programme_redeem(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The patient types the one-time code their employer gave them and agrees to be counted. Only
    for their own record."""
    body = json_body(request)
    with tenant_transaction():
        person = trust.own_person_of(auth.actor_public_id)
    if not person:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "write:person", subject_ref=person)
    with tenant_transaction(_platform_context()):
        made = programme.redeem(
            code=body.get("code"), clinic_tenant_ref=_my_customer(auth), person_ref=person,
            actor_ref=auth.actor_public_id,
        )
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action="programme.linked",
            object_type="programme_link",
            object_ref=made["id"],
            outcome=evidence.AuditOutcome.ALLOWED,
        )
    return JsonResponse({"programme": {k: v for k, v in made.items() if k != "person"}}, status=201)


@endpoint("POST")
def programme_leave(
    request: HttpRequest, auth: trust.Authenticated, link_id: str
) -> HttpResponse:
    """The patient stops being counted for that employer."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        person = programme.person_of(link_id, mine)
    if person not in reachable:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    _authorize(auth, "write:person", subject_ref=person)
    with tenant_transaction(_platform_context()):
        done = programme.leave(
            link_ref=link_id, clinic_tenant_ref=mine, person_refs=[person],
            actor_ref=auth.actor_public_id,
        )
    return JsonResponse({"programme": {k: v for k, v in done.items() if k != "person"}})


# ------------------------------------------------------------------ claims to a payer (DEV-308)
@endpoint("GET")
def claim_payers(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The payers a clinic may send a claim to (name and id only)."""
    _clinic_org(auth)
    payers = [
        {"id": t["public_id"], "name": t["name"]}
        for t in trust.list_tenants()
        if t["type"] == "payer" and t["status"] == "active"
    ]
    return JsonResponse({"payers": payers})


@endpoint("GET", "POST")
def claims_sent(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the claims this clinic sent. POST {payer, patient, member_id, category, label, amount,
    currency, service_date}: send one. The payer sees the member id, the service, the amount and
    the date, and nothing about the patient."""
    org = _clinic_org(auth)
    mine = _my_customer(auth)
    if request.method == "POST":
        body = json_body(request)
        person = text(body, "patient")
        _authorize(auth, "write:coverage", organization_public_id=org, subject_ref=person)
        target = next(
            (
                t
                for t in trust.list_tenants()
                if t["type"] == "payer"
                and t["status"] == "active"
                and t["public_id"] == body.get("payer")
            ),
            None,
        )
        if target is None:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["payer"])
        with tenant_transaction(_platform_context()):
            made = claim.send(
                from_tenant_ref=mine,
                from_name=auth.tenant_name,
                to_tenant_ref=target["public_id"],
                to_name=target["name"],
                person_ref=person,
                member_id=body.get("member_id"),
                category=body.get("category"),
                label=body.get("label"),
                amount_minor=body.get("amount"),
                currency=body.get("currency"),
                service_date=body.get("service_date"),
                actor_ref=auth.actor_public_id,
            )
        with tenant_transaction():
            evidence.record(
                actor_ref=auth.actor_public_id,
                action="claim.send",
                object_type="claim",
                object_ref=made["id"],
                outcome=evidence.AuditOutcome.ALLOWED,
            )
            claim.announce_consent_needed(claim_ref=made["id"], person_ref=person)
        out = [made]
        _add_names(auth, out, "patient")  # runs a policy check: outside a transaction
        return JsonResponse({"claim": out[0]}, status=201)
    _authorize(auth, "read:coverage", organization_public_id=org)
    paged = "limit" in request.GET or "cursor" in request.GET
    next_cursor: str | None = None
    with tenant_transaction(_platform_context()):
        if paged:
            rows, next_cursor = claim.page_sent(
                tenant_ref=mine,
                limit=pagination.limit_from(request.GET.get("limit")),
                cursor=request.GET.get("cursor") or None,
                actor_ref=auth.actor_public_id,
            )
        else:
            rows = claim.list_sent(tenant_ref=mine)
    _add_names(auth, rows, "patient")
    return JsonResponse({"claims": rows, **({"next_cursor": next_cursor} if paged else {})})


@endpoint("POST")
def claim_withdraw(
    request: HttpRequest, auth: trust.Authenticated, claim_id: str
) -> HttpResponse:
    """The clinic takes back a claim the payer has not decided."""
    org = _clinic_org(auth)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        person = claim.person_of(claim_id, mine)
    _authorize(auth, "write:coverage", organization_public_id=org, subject_ref=person)
    with tenant_transaction(_platform_context()):
        done = claim.withdraw(claim_ref=claim_id, tenant_ref=mine, actor_ref=auth.actor_public_id)
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action="claim.withdraw",
            object_type="claim",
            object_ref=claim_id,
            outcome=evidence.AuditOutcome.ALLOWED,
        )
    return JsonResponse({"claim": done})


@endpoint("POST")
def claim_attachments_sent(
    request: HttpRequest, auth: trust.Authenticated, claim_id: str
) -> HttpResponse:
    """The clinic attaches a supporting document (PDF, PNG, JPEG, up to 10 MB) to an
    undecided claim."""
    org = _clinic_org(auth)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        person = claim.person_of(claim_id, mine)
    _authorize(auth, "write:coverage", organization_public_id=org, subject_ref=person)
    filename = request.GET.get("filename") or ""
    data = request.read(10 * 1024 * 1024 + 1)
    with tenant_transaction(_platform_context()):
        att = claim.add_attachment(
            claim_ref=claim_id,
            tenant_ref=mine,
            filename=filename,
            data=data,
            actor_ref=auth.actor_public_id,
            side="clinic",
        )
    return JsonResponse({"attachment": att}, status=201)


@endpoint("GET", "DELETE")
def claim_attachment_sent_item(
    request: HttpRequest, auth: trust.Authenticated, claim_id: str, attachment_id: str
) -> HttpResponse:
    """The clinic downloads or deletes an attachment on a claim it sent."""
    org = _clinic_org(auth)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        person = claim.person_of(claim_id, mine)
    if request.method == "DELETE":
        _authorize(auth, "write:coverage", organization_public_id=org, subject_ref=person)
        with tenant_transaction(_platform_context()):
            claim.remove_attachment(
                claim_ref=claim_id,
                attachment_ref=attachment_id,
                tenant_ref=mine,
                actor_ref=auth.actor_public_id,
            )
        return JsonResponse({"deleted": True})
    _authorize(auth, "read:coverage", organization_public_id=org, subject_ref=person)
    with tenant_transaction(_platform_context()):
        name, ctype, data = claim.open_attachment(
            claim_ref=claim_id,
            attachment_ref=attachment_id,
            tenant_ref=mine,
            actor_ref=auth.actor_public_id,
        )
    response = HttpResponse(data, content_type=ctype)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@endpoint("GET")
def claim_history(request: HttpRequest, auth: trust.Authenticated, claim_id: str) -> HttpResponse:
    """The steps of a claim, for the clinic that sent it or the payer it went to."""
    area = home_for(auth.actor_type, list(auth.roles), auth.tenant_type)
    if area == "payer":
        org = _portal_org(auth, "payer")
    else:
        org = _clinic_org(auth)
    _authorize(auth, "read:coverage", organization_public_id=org)
    with tenant_transaction(_platform_context()):
        steps = claim.history(claim_ref=claim_id, tenant_ref=_my_customer(auth))
    return JsonResponse({"history": steps})


@endpoint("GET")
def claims_mine(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The claims made for the patient (or for a person a guardian acts for): what is claimed,
    from whom, how much and when, and whether they have agreed to it being sent."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    mine = _my_customer(auth)
    pg = _page_req(request, auth, "claims_mine")
    with tenant_transaction(_platform_context()):
        rows = claim.list_for_patients(tenant_ref=mine, person_refs=reachable, page=pg)
    allowed: dict[str, bool] = {}
    out = []
    for r in rows:
        if r["person"] not in allowed:
            allowed[r["person"]] = is_allowed(auth, "read:coverage", subject_ref=r["person"])
        if allowed[r["person"]]:
            out.append({k: v for k, v in r.items() if k != "person"})
    return JsonResponse(_with_cursor({"claims": out}, pg))


@endpoint("POST")
def claim_mine_step(
    request: HttpRequest, auth: trust.Authenticated, claim_id: str, step: str
) -> HttpResponse:
    """agree | decline | withdraw. Only the patient, or a guardian acting for them."""
    if step not in ("agree", "decline", "withdraw"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        person = claim.person_of(claim_id, mine)
    if person not in reachable:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    _authorize(auth, "read:coverage", subject_ref=person)
    with tenant_transaction(_platform_context()):
        done = claim.answer(
            claim_ref=claim_id,
            tenant_ref=mine,
            person_refs=[person],
            step=step,
            actor_ref=auth.actor_public_id,
        )
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action=f"claim.consent_{step}",
            object_type="claim",
            object_ref=claim_id,
            outcome=evidence.AuditOutcome.ALLOWED,
        )
    return JsonResponse({"claim": {k: v for k, v in done.items() if k != "person"}})


@endpoint("GET")
def claims_incoming(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The claims sent to this payer: member id, service, amount, date. Never the patient."""
    org = _portal_org(auth, "payer")
    _authorize(auth, "read:coverage", organization_public_id=org)
    paged = "limit" in request.GET or "cursor" in request.GET
    next_cursor: str | None = None
    with tenant_transaction(_platform_context()):
        if paged:
            rows, next_cursor = claim.page_incoming(
                tenant_ref=_my_customer(auth),
                state=request.GET.get("state") or None,
                limit=pagination.limit_from(request.GET.get("limit")),
                cursor=request.GET.get("cursor") or None,
                actor_ref=auth.actor_public_id,
            )
        else:
            rows = claim.list_incoming(
                tenant_ref=_my_customer(auth), state=request.GET.get("state") or None
            )
    return JsonResponse({"claims": rows, **({"next_cursor": next_cursor} if paged else {})})


@endpoint("GET")
def claim_cover_check(
    request: HttpRequest, auth: trust.Authenticated, claim_id: str
) -> HttpResponse:
    """Is the claim's member covered for that kind of service on that day? Uses the payer's own
    plans and members, and always answers with a reason."""
    org = _portal_org(auth, "payer")
    decision = _authorize(auth, "read:coverage", organization_public_id=org)
    with tenant_transaction(_platform_context()):
        found = claim.member_of(claim_id, _my_customer(auth))
    with tenant_transaction():
        answer = sponsor.check_entitlement(
            organization_ref=org,
            member_id=found["member_id"],
            category=found["category"],
            on=found["service_date"],
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"check": answer})


@endpoint("GET")
def claim_attachment_incoming_item(
    request: HttpRequest, auth: trust.Authenticated, claim_id: str, attachment_id: str
) -> HttpResponse:
    """The payer downloads a clean supporting attachment on a claim sent to them."""
    org = _portal_org(auth, "payer")
    _authorize(auth, "read:coverage", organization_public_id=org)
    mine = _my_customer(auth)
    with tenant_transaction(_platform_context()):
        name, ctype, data = claim.open_attachment(
            claim_ref=claim_id,
            attachment_ref=attachment_id,
            tenant_ref=mine,
            actor_ref=auth.actor_public_id,
        )
    response = HttpResponse(data, content_type=ctype)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@endpoint("POST")
def claim_payer_step(
    request: HttpRequest, auth: trust.Authenticated, claim_id: str, step: str
) -> HttpResponse:
    """approve | decline {reason} | pay {payment_ref}. Reasons: not_covered, member_not_found,
    duplicate, missing_information, other."""
    org = _portal_org(auth, "payer")
    _authorize(auth, "attest:coverage", organization_public_id=org)
    body = json_body(request)
    with tenant_transaction(_platform_context()):
        done = claim.payer_step(
            claim_ref=claim_id,
            tenant_ref=_my_customer(auth),
            step=step,
            actor_ref=auth.actor_public_id,
            reason=body.get("reason"),
            payment_ref=body.get("payment_ref"),
            amount_paid_minor=body.get("amount_paid_minor"),
            paid_on=body.get("paid_on"),
            remittance_advice=body.get("remittance_advice"),
        )
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action=f"claim.{step}",
            object_type="claim",
            object_ref=claim_id,
            outcome=evidence.AuditOutcome.ALLOWED,
        )
    return JsonResponse({"claim": done})


SANDBOX_MAX_BYTES = 64 * 1024


def _developer(auth: trust.Authenticated) -> str:
    """A partner developer (R10) of a partner customer; returns their organisation."""
    org = _portal_org(auth, "developer")
    if "R10" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:integration", organization_public_id=org)
    return org


@endpoint("GET")
def portal_sandbox(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """A developer's sandbox (DEV-329): fixed synthetic patients, each with the FHIR order Zoiko
    would send and an example report back, and whether the intake would accept or hold it.
    Nothing here is real or stored."""
    _developer(auth)
    return JsonResponse({"patients": synthetic.sandbox()})


@endpoint("POST")
def portal_sandbox_check(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Check a developer's own FHIR resource against the intake's rules (UK Core). Says whether it
    would be accepted or held, and why. Nothing is kept or logged except that a check was made."""
    _developer(auth)
    if len(request.body) > SANDBOX_MAX_BYTES:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["body"])
    try:  # FHIR JSON, or an HL7 v2 ORU^R01 message read the same way as the intake (DEV-340)
        resource = integration.parse_payload((request.body or b"null").decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        text = str(exc)[:200] if (request.body or b"").startswith(b"MSH") else "not valid JSON"
        return JsonResponse({"result": "held", "issues": [
            {"path": "", "code": "invalid", "text": text}]})
    return JsonResponse(synthetic.check(resource))


@endpoint("GET", "POST")
def portal_order_system(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the system this lab, pharmacy or home-care partner receives orders in, and the ones it
    may choose. POST {system}: choose one ("" = work in the partner app only). Only a system Zoiko
    has certified and put live may be chosen (DEV-230); the pretend partner only in development
    (DEV-323)."""
    from modules.connector import api as connector

    org = _portal_org(auth, "partner")
    choices = [
        {"key": key, "simulated": connector.is_simulated(key)}
        for key in connector.registered()
        if connector.is_simulated(key) or connector.is_live(key)
    ]
    if request.method == "POST":
        _authorize(auth, "write:partner", organization_public_id=org)
        system = json_body(request).get("system", "")
        if system and system not in {c["key"] for c in choices}:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["system"])
        with tenant_transaction():
            trust.set_booking_system(org, system, by=auth.actor_public_id)
    else:
        _authorize(auth, "read:partner", organization_public_id=org)
    with tenant_transaction():
        current = trust.booking_system_of(org)
    return JsonResponse({"system": current, "choices": choices})


@endpoint("GET")
def exchange_incoming(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The requests sent to this partner. Never shows the clinic's patient reference."""
    org = _portal_org(auth, "partner")
    _authorize(auth, "read:partner", organization_public_id=org)
    paged = "limit" in request.GET or "cursor" in request.GET
    next_cursor: str | None = None
    with tenant_transaction(_platform_context()):
        if paged:
            rows, next_cursor = exchange.page_incoming(
                tenant_ref=_my_customer(auth),
                state=request.GET.get("state") or None,
                limit=pagination.limit_from(request.GET.get("limit")),
                cursor=request.GET.get("cursor") or None,
                actor_ref=auth.actor_public_id,
            )
        else:
            rows = exchange.list_incoming(
                tenant_ref=_my_customer(auth), state=request.GET.get("state") or None
            )
    return JsonResponse({"requests": rows, **({"next_cursor": next_cursor} if paged else {})})


def _alert_critical(request_id: str, partner_ref: str) -> None:
    """A critical result tells the person who ordered it, by email to a confirmed address. The
    delivery is already done: a failure here is logged by class only and never undoes it."""
    try:
        with tenant_transaction(_platform_context()):
            target = exchange.critical_target(request_ref=request_id, tenant_ref=partner_ref)
        if target is not None:
            with tenant_transaction(trust.context_for(target["clinic"])):
                comms.alert_critical_result(target["orderer"])
    except Exception as exc:  # noqa: BLE001 - never let an alert undo a delivery
        logging.getLogger("zoiko.alerts").warning("critical alert failed: %s", type(exc).__name__)


@endpoint("POST")
def exchange_incoming_step(
    request: HttpRequest, auth: trust.Authenticated, request_id: str, step: str
) -> HttpResponse:
    """accept | decline {note} | start | deliver {result_ref} | correct {result_ref, summary,
    flag, correction}. Only the role that handles this kind of request (lab, pharmacy or home
    care) may answer it. A correction is a new version; the earlier one is kept (DEV-325)."""
    org = _portal_org(auth, "partner")
    mine = _my_customer(auth)
    body = json_body(request)
    with tenant_transaction(_platform_context()):
        kind = exchange.kind_of(request_id, mine)
    if exchange.role_for(kind) not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "write:partner", organization_public_id=org)
    with tenant_transaction(_platform_context()):
        if step == "correct":
            done = exchange.correct_result(
                request_ref=request_id,
                tenant_ref=mine,
                actor_ref=auth.actor_public_id,
                result_ref=body.get("result_ref"),
                summary=body.get("summary"),
                flag=body.get("flag"),
                correction=body.get("correction") or "corrected",
                values=body.get("values"),
            )
        else:
            done = exchange.partner_step(
                request_ref=request_id,
                tenant_ref=mine,
                step=step,
                actor_ref=auth.actor_public_id,
                note=body.get("note"),
                result_ref=body.get("result_ref"),
                summary=body.get("summary"),
                flag=body.get("flag"),
                values=body.get("values"),
            )
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action=f"exchange.{step}",
            object_type="exchange_request",
            object_ref=request_id,
            outcome=evidence.AuditOutcome.ALLOWED,
        )
    changed = done.pop("changed", True)
    if changed and step in ("deliver", "correct") and done.get("result_flag") == "critical":
        _alert_critical(request_id, mine)
    return JsonResponse({"request": done})


# ------------------------------------------------------------------ notifications (patients)
def _notification_json(n: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": n["public_id"],
        "person": n["person_ref"],
        "kind": n["kind"],
        "title": n["title"],
        "body": n["body"],
        "booking": n["booking_ref"],
        "created_at": n["created_at"],
        "read": n["read_at"] is not None,
    }


@endpoint("GET")
def notifications(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Notifications for every patient this login may read (own record, or a guardian's grant)."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    people = [p for p in reachable if is_allowed(auth, "read:comms", subject_ref=p)]
    with tenant_transaction():
        items = comms.list_for_persons(people)
        unread = comms.unread_count(people)
    return JsonResponse({"notifications": [_notification_json(n) for n in items], "unread": unread})


@endpoint("POST")
def notification_read(
    request: HttpRequest, auth: trust.Authenticated, notification_id: str
) -> HttpResponse:
    with tenant_transaction():
        person = comms.get_notification(notification_id)["person_ref"]
    decision = _authorize(auth, "write:comms", subject_ref=person)
    with tenant_transaction():
        done = comms.mark_read(
            notification_ref=notification_id,
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"notification": _notification_json(done)})


# ------------------------------------------------------------------ email links and reset
def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
def auth_verify_email(request: HttpRequest) -> HttpResponse:
    """Confirm an email address from the link we sent. One generic error for a bad link."""
    trust.verify_email(text(json_body(request), "token"))
    return _no_store(JsonResponse({"verified": True}))


@csrf_exempt
@require_POST
def auth_forgot_password(request: HttpRequest) -> HttpResponse:
    """Always the same answer, whether or not an email was sent."""
    body = json_body(request)
    trust.request_password_reset(tenant_name=text(body, "organization"), login=text(body, "login"))
    return _no_store(JsonResponse({"sent": True}, status=202))


@csrf_exempt
@require_POST
def auth_reset_password(request: HttpRequest) -> HttpResponse:
    body = json_body(request)
    password = body.get("password")
    if not isinstance(password, str):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["password"])
    trust.reset_password(text(body, "token"), password)
    return _no_store(JsonResponse({"reset": True}))


@endpoint("POST")
def auth_change_password(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The signed-in person changes their own password. Needs the current one. Every other device
    is signed out and this one gets a fresh token. A wrong current password is a 422, not a 401,
    so the app does not mistake it for an expired session."""
    body = json_body(request)
    current, new = body.get("current_password"), body.get("new_password")
    if not isinstance(current, str) or not isinstance(new, str):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["current_password", "new_password"])
    with tenant_transaction():
        result = trust.change_password(
            auth.actor_public_id, current_password=current, new_password=new,
            second_step_at=auth.second_step_at,
        )
    if not result.ok or result.expires_at is None:  # raised AFTER the transaction, so the
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["current_password"])  # counter stays
    return _no_store(
        JsonResponse({"token": result.token, "expires_at": result.expires_at.isoformat()})
    )


@endpoint("POST")
def auth_set_email(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The signed-in person sets or changes their email address. Needs the current password.
    The new address is unverified until its owner opens the emailed link. A wrong password is a
    422 (not a 401), and counts towards the same lockout as sign-in."""
    body = json_body(request)
    email, current = body.get("email"), body.get("current_password")
    if not isinstance(email, str) or not isinstance(current, str):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["email", "current_password"])
    with tenant_transaction():
        result = trust.set_email(auth.actor_public_id, new_email=email, current_password=current)
        status = trust.email_status(auth.actor_public_id)
    if not result.ok:  # raised AFTER the transaction, so the counter is kept
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["current_password"])
    return _no_store(JsonResponse({**status, "sent": result.sent}))


@endpoint("POST", "DELETE")
def auth_display_name(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """POST {name}: staff, doctors and platform users choose the name they are shown by (patients
    and guardians cannot: their name is a patient detail). DELETE: go back to the login. The name
    is the person's own choice and is NOT verified."""
    with tenant_transaction():
        if request.method == "DELETE":
            trust.clear_display_name(auth.actor_public_id)
            return JsonResponse({"name": ""})
        body = json_body(request)
        name = trust.set_display_name(auth.actor_public_id, body.get("name"))
    return JsonResponse({"name": name})


@endpoint("POST")
def auth_resend_verification(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    with tenant_transaction():
        sent = trust.send_verification(auth.actor_public_id)
    return _no_store(JsonResponse({"sent": sent}, status=202))


# ------------------------------------------------------------------ team management (admins)
@endpoint("POST")
def staff_invite(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    body = json_body(request)
    org = _staff_only(auth)
    decision = _authorize(auth, "write:staff", organization_public_id=org)
    require_recent_second_step(auth)          # a risky act: the code again if stale (DEV-380)
    with tenant_transaction():
        member = trust.invite_staff(
            email=text(body, "email"),
            role_code=text(body, "role"),
            organization_public_id=str(org),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"member": member}, status=201)


@endpoint("POST")
def staff_member_status(
    request: HttpRequest, auth: trust.Authenticated, member_id: str
) -> HttpResponse:
    body = json_body(request)
    org = _staff_only(auth)
    decision = _authorize(auth, "write:staff", organization_public_id=org)
    require_recent_second_step(auth)
    with tenant_transaction():
        done = trust.set_member_status(
            member_id,
            text(body, "status"),
            organization_public_id=str(org),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"member": done})


@endpoint("POST")
def staff_member_resend(
    request: HttpRequest, auth: trust.Authenticated, member_id: str
) -> HttpResponse:
    org = _staff_only(auth)
    decision = _authorize(auth, "write:staff", organization_public_id=org)
    with tenant_transaction():
        trust.resend_invitation(
            member_id,
            organization_public_id=str(org),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"sent": True}, status=202)


# ------------------------------------------------------------------ doctor (clinician) screens
def _clinician_only(auth: trust.Authenticated) -> None:
    """These endpoints serve the doctor app: doctors only, whatever the policy table allows."""
    if home_for(auth.actor_type, list(auth.roles), auth.tenant_type) != "clinician":
        raise ZoikoError(ErrorCode.POLICY_DENIED)


@endpoint("GET")
def clinician_schedule(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The doctor's own confirmed appointments. Each patient is checked by the policy engine:
    a doctor sees a patient only through a care relationship."""
    _clinician_only(auth)
    with tenant_transaction():
        rows = scheduling.list_bookings_for_provider(auth.actor_public_id)
    patients = {r["person_ref"] for r in rows}
    allowed = {p for p in sorted(patients) if is_allowed(auth, "read:booking", subject_ref=p)}
    with tenant_transaction():
        orgs, labels = trust.organization_names(), trust.actor_labels()
        svc: dict[str, Any] = {}
        loc: dict[str, Any] = {}
        checked = trust.verified_providers()
        out = [
            _booking_json(r, svc, loc, orgs, labels, checked)
            for r in rows
            if r["person_ref"] in allowed
        ]
    return JsonResponse({"appointments": _add_names(auth, out)})


@endpoint("GET")
def clinician_patients(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The doctor's patients: people they hold an active care relationship with."""
    _clinician_only(auth)
    with tenant_transaction():
        links = trust.care_patients(auth.actor_public_id)
        visits = scheduling.list_bookings_for_provider(auth.actor_public_id)
    count: dict[str, int] = {}
    for v in visits:
        count[v["person_ref"]] = count.get(v["person_ref"], 0) + 1
    out = [
        {
            "id": link["person_ref"],
            "basis": link["basis"],
            "since": link["since"],
            "appointments": count.get(link["person_ref"], 0),
        }
        for link in links
        if is_allowed(auth, "read:person", subject_ref=link["person_ref"])
    ]
    return JsonResponse({"patients": _add_names(auth, out, "id")})


# ------------------------------------------------------------------ my care team, who looked at me
def _my_people(auth: trust.Authenticated, operation: str) -> list[str]:
    """The patients this login may use `operation` on (themselves, or people they act for)."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    return [p for p in reachable if is_allowed(auth, operation, subject_ref=p)]


def _section(view: Any, request: HttpRequest, auth: trust.Authenticated, key: str) -> Any:
    """What one of the patient's own screens returns, so the export holds exactly what the person
    can already see there (same rights checks), or says the section is not available."""
    try:
        response = view.__wrapped__(request, auth)
    except ZoikoError:
        return {"available": False}
    if response.status_code != 200:
        return {"available": False}
    return json.loads(response.content)[key]


@endpoint("GET", "POST")
def my_email_notices(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """A patient's own switch for short emails about their care (off until they turn it on).
    GET: `{"enabled", "can_email"}`: can_email says whether they have a CONFIRMED address, since
    nothing is ever sent to an unconfirmed one. POST `{"enabled": true|false}` changes it.
    Patients only: guardians are not emailed about someone else's care."""
    if home_for(auth.actor_type, list(auth.roles), auth.tenant_type) != "patient":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if request.method == "POST":
        body = json_body(request)
        if "enabled" not in body and "quiet" not in body and "texts" not in body:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["enabled"])
        if "texts" in body:
            texts = body.get("texts")
            if not isinstance(texts, bool):
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["texts"])
            with tenant_transaction():
                comms.set_texts(auth.actor_public_id, texts)
        if "enabled" in body:
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["enabled"])
            with tenant_transaction():
                comms.set_email_notices(auth.actor_public_id, enabled)
        if "quiet" in body:  # {"start": "22:00", "end": "07:00", "zone": "Europe/London"} or null
            window = body.get("quiet")
            if window is not None and not isinstance(window, dict):
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["quiet"])
            with tenant_transaction():
                comms.set_quiet_hours(
                    auth.actor_public_id,
                    (window or {}).get("start") if window else None,
                    (window or {}).get("end") if window else None,
                    (window or {}).get("zone") if window else None,
                )
    with tenant_transaction():
        persons = trust.reachable_persons(auth.actor_public_id)
        own = [
            p
            for p in persons
            if (trust.verified_login_email_of_person(p) or ("",))[0] == auth.actor_public_id
        ]
        return JsonResponse(
            {
                "enabled": comms.email_notices_enabled(auth.actor_public_id),
                "can_email": bool(own),
                "quiet": comms.quiet_hours_of(auth.actor_public_id),
                "texts": comms.texts_enabled(auth.actor_public_id),
                "phone_verified": comms.phone_verified(
                    auth.actor_public_id, _own_phone(auth.actor_public_id)
                ),
            }
        )


def _own_phone(actor_ref: str) -> str:
    """The phone number on this login's own patient profile ("" if none). Bound tenant needed."""
    own = trust.own_person_of(actor_ref)
    if not own:
        return ""
    return (trust.get_person_profiles([own]).get(own) or {}).get("phone", "")


def _patient_only(auth: trust.Authenticated) -> None:
    if home_for(auth.actor_type, list(auth.roles), auth.tenant_type) != "patient":
        raise ZoikoError(ErrorCode.POLICY_DENIED)


@endpoint("POST")
def my_phone_check(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Text a six-digit code to the number on the patient's own profile (DEV-198). It must start
    with + and the country code. At most three codes an hour."""
    _patient_only(auth)
    with tenant_transaction():
        phone = _own_phone(auth.actor_public_id)
        if not phone:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["phone"])
        sent = comms.send_code(auth.actor_public_id, phone)
    return JsonResponse({"sent": True, **sent}, status=201)


@endpoint("GET", "POST")
def staff_alert_phone(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """A clinician's (or clinic staff member's) own mobile number for urgent alerts (DEV-334).
    GET: masked number, whether it is proved, whether critical-result texts are on.
    POST {number}: save it and text a code. POST {critical_texts: bool}: turn the texts on or off
    (on only once the number is proved)."""
    area = home_for(auth.actor_type, list(auth.roles), auth.tenant_type)
    if area not in ("clinician", "operations"):
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    alerts = comms.alert_phone
    if request.method == "POST":
        body = json_body(request)
        with tenant_transaction():
            if "number" in body:
                return JsonResponse({"alert_phone": alerts.set_number(
                    auth.actor_public_id, body.get("number"))}, status=201)
            if "hold_texts" in body:
                if "R13" not in auth.roles:
                    raise ZoikoError(ErrorCode.POLICY_DENIED)  # administrators only
                if body.get("hold_texts") is True and not alerts.status(
                        auth.actor_public_id)["verified"]:
                    raise ZoikoError(ErrorCode.CONFLICT_VERSION)
                return JsonResponse({"alert_phone": alerts.set_hold_texts(
                    auth.actor_public_id, body.get("hold_texts"))})
            if "critical_texts" in body:
                if body.get("critical_texts") is True and not alerts.status(
                        auth.actor_public_id)["verified"]:
                    raise ZoikoError(ErrorCode.CONFLICT_VERSION)  # prove the number first
                return JsonResponse({"alert_phone": alerts.set_critical_texts(
                    auth.actor_public_id, body.get("critical_texts"))})
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["number"])
    with tenant_transaction():
        return JsonResponse({"alert_phone": alerts.status(auth.actor_public_id)})


@endpoint("POST")
def staff_alert_phone_confirm(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The clinician types back the code texted to their alert number."""
    area = home_for(auth.actor_type, list(auth.roles), auth.tenant_type)
    if area not in ("clinician", "operations"):
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with tenant_transaction():
        right = comms.alert_phone.confirm(auth.actor_public_id, json_body(request).get("code"))
    if not right:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["code"])
    with tenant_transaction():
        return JsonResponse({"alert_phone": comms.alert_phone.status(auth.actor_public_id)})


@endpoint("POST")
def my_phone_confirm(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The patient types the code back: their number is then verified for texts."""
    _patient_only(auth)
    body = json_body(request)
    with tenant_transaction():
        right = comms.confirm_code(
            auth.actor_public_id, _own_phone(auth.actor_public_id), body.get("code")
        )
    if not right:  # raised only now, so the used-up try stays counted
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["code"])
    return JsonResponse({"phone_verified": True})


def _export_history(auth: trust.Authenticated, booking_id: str) -> dict[str, Any]:
    try:
        return {"appointment": booking_id, **_history_payload(auth, booking_id)}
    except ZoikoError:
        return {"appointment": booking_id, "available": False}


@endpoint("GET")
def my_export(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """A patient's (or guardian's) data as one JSON file: the same information the app shows them,
    gathered from the same screens so the same rights apply, with the message texts included.
    Only for patient accounts: a staff account would otherwise receive the clinic's records."""
    if home_for(auth.actor_type, list(auth.roles), auth.tenant_type) != "patient":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    out: dict[str, Any] = {
        "exported_at": utc_now().isoformat(),
        "format": "zoiko-access-export-1",
        "note": "Everything here is information you can already see in the app.",
        "appointments": _section(bookings, request, auth, "bookings"),
        "referrals": _section(my_referrals, request, auth, "referrals"),
        "care_team": _section(my_care_team, request, auth, "people"),
        "access_log": _section(my_access_log, request, auth, "entries"),
        "profiles": _section(my_profiles, request, auth, "profiles"),
        "family": _section(family_members, request, auth, "family"),
        "insurance": _section(coverage_entries, request, auth, "coverage"),
        "waiting_list": _section(waitlist_entries, request, auth, "entries"),
        "notifications": _section(notifications, request, auth, "notifications"),
    }
    # The steps of each appointment, worded as the app shows them to the patient (never which staff
    # member acted). Appointments made before steps were recorded simply have an empty list.
    appointments = out["appointments"]
    if isinstance(appointments, list):
        out["appointment_history"] = [_export_history(auth, b["id"]) for b in appointments]
    else:
        out["appointment_history"] = appointments
    threads = _section(message_threads, request, auth, "threads")
    conversations: Any = threads
    if isinstance(threads, list):
        conversations = []
        for t in threads:
            with tenant_transaction():
                messages = messaging.read_messages(t["id"])
            conversations.append(
                {
                    **t,
                    "messages": [
                        {"side": m["side"], "at": m["at"], "body": m["body"]} for m in messages
                    ],
                }
            )
    out["conversations"] = conversations
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action="data.export",
            object_type="actor",
            object_ref=auth.actor_public_id,
            outcome=evidence.AuditOutcome.ALLOWED,
        )
    response = JsonResponse(out, json_dumps_params={"indent": 2})
    response["Content-Disposition"] = 'attachment; filename="my-zoiko-data.json"'
    return response


@endpoint("GET")
def my_care_team(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Who is treating each patient this login may read."""
    people = _my_people(auth, "read:person")
    with tenant_transaction():
        labels = trust.actor_labels()
        out = [
            {
                "person": p,
                "team": [
                    {
                        "clinician": labels.get(m["actor_ref"], ""),
                        "basis": m["basis"],
                        "since": m["since"],
                        "until": m["until"] or None,
                    }
                    for m in trust.care_team_of(p)
                ],
            }
            for p in people
        ]
    return JsonResponse({"people": out})


@endpoint("GET")
def my_access_log(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Who looked at (or tried to look at) the information of each patient this login may read,
    newest first. The patient's own looks are left out: this is about OTHER people."""
    people = _my_people(auth, "read:evidence")
    with tenant_transaction():
        labels = trust.actor_labels()
        rows = policy.decisions_about(people, exclude_actors=[auth.actor_public_id], limit=100)
    return JsonResponse(
        {
            "entries": [
                {
                    "at": r["at"],
                    "who": labels.get(str(r["actor"]), "The system"),
                    "role": r["role"] or None,
                    "operation": r["operation"],
                    "purpose": r["purpose"],
                    "allowed": r["allowed"],
                    "person": r["subject"],
                }
                for r in rows
            ]
        }
    )


# ------------------------------------------------------------------ messages
def _is_staff(auth: trust.Authenticated) -> bool:
    return home_for(auth.actor_type, list(auth.roles), auth.tenant_type) == "operations"


def _clinic_side(auth: trust.Authenticated) -> bool:
    """On the clinic's side of a conversation: clinic staff, or a doctor (DEV-162)."""
    return home_for(auth.actor_type, list(auth.roles), auth.tenant_type) in (
        "operations",
        "clinician",
    )


def _thread_json(t: dict[str, Any], staff: bool) -> dict[str, Any]:
    return {
        "id": t["public_id"],
        "subject": t["subject"],
        "status": t["status"],
        "patient": t["person_ref"],
        "last_message_at": t["last_message_at"],
        "unread": t["staff_unread"] if staff else t["patient_unread"],
        # What THIS login has not opened (others' replies or openings never clear it for them).
        "unread_for_me": t.get(
            "unread_for_me", t["staff_unread"] if staff else t["patient_unread"]
        ),
    }


def _once(
    request: HttpRequest,
    auth: trust.Authenticated,
    scope: str,
    body: dict[str, Any],
    work: Callable[[], dict[str, Any]],
) -> IdempotentOutcome:
    """With an Idempotency-Key header, do the work once however often it is sent (DEV-148); with
    none, just do it, so older clients keep working."""
    key = request.headers.get("Idempotency-Key", "")
    if not key:
        return IdempotentOutcome(replayed=False, result=work())
    return run_once(
        scope=f"actor:{auth.actor_public_id} {scope}",
        key=key,
        payload=body,
        retain_for=IDEMPOTENCY_RETENTION,
        work=work,
    )


@endpoint("GET", "POST")
def message_threads(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the threads this login may see (a clinic: its own; a patient: their people's).
    POST: a patient or guardian writes to their clinic."""
    org = _catalog_org(auth)
    staff = _is_staff(auth)
    if request.method == "POST":
        body = json_body(request)
        if staff or org is None or not set(auth.roles) & {"R01", "R02"}:
            raise ZoikoError(ErrorCode.POLICY_DENIED)  # only patients and guardians start threads
        with tenant_transaction():
            person = text(body, "person", required=False)
            if not person:
                mine = trust.reachable_persons(auth.actor_public_id)
                if len(mine) != 1:
                    own = trust.own_person_of(auth.actor_public_id)
                    if not own:
                        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["person"])
                    person = own
                else:
                    person = mine[0]
        decision = _authorize(auth, "write:comms", organization_public_id=org, subject_ref=person)
        with tenant_transaction():
            outcome = _once(
                request,
                auth,
                "POST /messages/threads",
                body,
                lambda: {
                    "thread": messaging.start_thread(
                        person_ref=person,
                        organization_ref=org,
                        subject=text(body, "subject"),
                        body=text(body, "body"),
                        actor_public_id=auth.actor_public_id,
                        decision=decision,
                    )["public_id"]
                },
            )
            made = messaging.get_thread(str(outcome.result["thread"]))
        return JsonResponse(
            {"thread": _thread_json(made, False)}, status=200 if outcome.replayed else 201
        )
    pg = _page_req(request, auth, "messages.threads")
    if staff:
        _staff_only(auth)
        if org is None or not is_allowed(auth, "read:comms", organization_public_id=org):
            return JsonResponse(_with_cursor({"threads": []}, pg))
        with tenant_transaction():
            rows = messaging.list_threads(
                organization_ref=org, viewer_ref=auth.actor_public_id, viewer_side="staff", page=pg
            )
    else:
        with tenant_transaction():
            reachable = trust.reachable_persons(auth.actor_public_id)
        persons = [p for p in reachable if is_allowed(auth, "read:comms", subject_ref=p)]
        with tenant_transaction():
            rows = (
                messaging.list_threads(
                    person_refs=persons, viewer_ref=auth.actor_public_id,
                    viewer_side="patient", page=pg,
                )
                if persons
                else []
            )
    out = [_thread_json(t, staff) for t in rows]
    if staff:
        _add_names(auth, out, "patient")  # staff see the name where allowed
    return JsonResponse(_with_cursor({"threads": out}, pg))


def _thread_access(
    auth: trust.Authenticated, thread_id: str, operation: str
) -> tuple[dict[str, Any], str | None, policy.Decision]:
    org = _catalog_org(auth)
    with tenant_transaction():
        thread = messaging.get_thread(thread_id)
    if _clinic_side(auth) and thread["organization_ref"] != org:
        raise ZoikoError(ErrorCode.NOT_FOUND)  # another organization's thread
    decision = _authorize(
        auth, operation, organization_public_id=org, subject_ref=thread["person_ref"]
    )
    return thread, org, decision


@endpoint("GET")
def message_thread(request: HttpRequest, auth: trust.Authenticated, thread_id: str) -> HttpResponse:
    """One thread with its messages. Opening it marks it read for the side that opened it."""
    staff = _clinic_side(auth)
    _thread_access(auth, thread_id, "read:comms")
    with tenant_transaction():
        opened = messaging.open_thread(
            thread_id, side="staff" if staff else "patient", viewer_ref=auth.actor_public_id
        )
        labels = trust.actor_labels()
    out = _thread_json(opened["thread"], staff)
    if staff:
        _add_names(auth, [out], "patient")
    out["messages"] = [
        {
            "id": m["public_id"],
            "side": m["side"],
            "body": m["body"],
            "at": m["at"],
            "mine": m["author_ref"] == auth.actor_public_id,
            # When the OTHER side first opened a message written by this reader's own side. It says
            # a person opened it: not that it was understood, and never a clinical review.
            "seen_at": m["seen_at"] if m["side"] == ("staff" if staff else "patient") else None,
            "from": labels.get(m["author_ref"], "") if m["side"] == "staff" else "",
        }
        for m in opened["messages"]
    ]
    return JsonResponse({"thread": out})


@endpoint("POST")
def message_reply(request: HttpRequest, auth: trust.Authenticated, thread_id: str) -> HttpResponse:
    body = json_body(request)
    _, org, decision = _thread_access(auth, thread_id, "write:comms")
    with tenant_transaction():
        _once(
            request,
            auth,
            f"POST /messages/{thread_id}/reply",
            body,
            lambda: {
                "thread": messaging.reply(
                    thread_ref=thread_id,
                    body=text(body, "body"),
                    actor_public_id=auth.actor_public_id,
                    decision=decision,
                    organization_public_id=org,
                )["public_id"]
            },
        )
        done = messaging.get_thread(thread_id)
    return JsonResponse({"thread": _thread_json(done, _clinic_side(auth))})


@endpoint("POST")
def message_close(request: HttpRequest, auth: trust.Authenticated, thread_id: str) -> HttpResponse:
    org = _staff_only(auth)
    _, _, decision = _thread_access(auth, thread_id, "write:comms")
    with tenant_transaction():
        done = messaging.close_thread(
            thread_ref=thread_id,
            actor_public_id=auth.actor_public_id,
            decision=decision,
            organization_public_id=org or "",
        )
    return JsonResponse({"thread": _thread_json(done, True)})


@endpoint("GET")
def clinician_messages(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The doctor's conversations: only those of patients they have a care relationship with,
    and only in their own organisation. Each patient is checked by the policy engine."""
    _clinician_only(auth)
    org = _catalog_org(auth)
    with tenant_transaction():  # a doctor's patients come from care relationships
        mine = sorted({c["person_ref"] for c in trust.care_patients(auth.actor_public_id)})
    persons = [
        p for p in mine if is_allowed(auth, "read:comms", organization_public_id=org, subject_ref=p)
    ]
    pg = _page_req(request, auth, "clinician.messages")
    if org is None or not persons:
        return JsonResponse(_with_cursor({"threads": []}, pg))
    with tenant_transaction():
        rows = messaging.list_threads(
            person_refs=persons,
            organization_ref=org,
            viewer_ref=auth.actor_public_id,
            viewer_side="staff",
            page=pg,
        )
    out = [_thread_json(t, True) for t in rows]
    _add_names(auth, out, "patient")
    return JsonResponse(_with_cursor({"threads": out}, pg))


# ------------------------------------------------------------------ family members
def _family_json(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": g["public_id"],
        "status": g["status"],
        "relationship": g["relationship"],
        "person": g["person_ref"],
        "name": g["name"],
        "birth_date": g["birth_date"],
        "scopes": g.get("scopes", []),
        "proof_kind": g.get("proof_kind", ""),
        "valid_until": g["valid_until"],
        "requested_at": g["created_at"],
    }


@endpoint("GET", "POST")
def family_members(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the people this patient has asked to act for (waiting or approved). POST: ask to act
    for a new person: nothing is allowed until clinic staff approve."""
    if request.method == "GET":
        with tenant_transaction():
            rows = trust.list_family(auth.actor_public_id)
        return JsonResponse({"family": [_family_json(g) for g in rows]})
    body = json_body(request)
    org = _catalog_org(auth)
    with tenant_transaction():
        own = trust.own_person_of(auth.actor_public_id)
    if not own:
        raise ZoikoError(ErrorCode.POLICY_DENIED)  # only a patient with their own record
    decision = _authorize(auth, "write:person", organization_public_id=org, subject_ref=own)
    with tenant_transaction():
        made = trust.request_family_member(
            requester_public_id=auth.actor_public_id,
            own_person_ref=own,
            relationship=text(body, "relationship"),
            name=text(body, "name"),
            birth_date=text(body, "birth_date"),
            decision=decision,
            organization_public_id=org,
        )
    return JsonResponse({"request": _family_json(made)}, status=201)


@endpoint("POST")
def family_withdraw(request: HttpRequest, auth: trust.Authenticated, grant_id: str) -> HttpResponse:
    """Withdraw a waiting request, or stop acting for someone. Only your own."""
    with tenant_transaction():
        done = trust.withdraw_family_member(
            grant_ref=grant_id, requester_public_id=auth.actor_public_id
        )
    return JsonResponse({"request": _family_json(done)})


@endpoint("GET")
def staff_family_requests(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Waiting requests, oldest first, for clinic staff to check and decide."""
    org = _staff_only(auth)
    _authorize(auth, "read:person", organization_public_id=org)
    with tenant_transaction():
        rows = trust.pending_family_requests()
        labels = trust.actor_labels()
        owns = {g["delegate_ref"]: trust.own_person_of(g["delegate_ref"]) for g in rows}
        approved = trust.approved_family_grants()
        for g in approved:
            owns.setdefault(g["delegate_ref"], trust.own_person_of(g["delegate_ref"]))
    names = _names(auth, {p for p in owns.values() if p})

    def shown(g: dict[str, Any]) -> dict[str, Any]:
        return {
            **_family_json(g),
            "proof_ref": g.get("proof_ref", ""),
            "requested_by": labels.get(g["delegate_ref"], ""),
            "requested_by_name": names.get(owns.get(g["delegate_ref"], "")) or None,
        }

    return JsonResponse(
        {"requests": [shown(g) for g in rows], "approved": [shown(g) for g in approved]}
    )


def _decide_family(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str, approve: bool
) -> HttpResponse:
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    body = json_body(request)
    with tenant_transaction():
        grant = trust.get_family_grant(grant_id)
    decision = _authorize(
        auth, "write:person", organization_public_id=org, subject_ref=grant["person_ref"]
    )
    with tenant_transaction():
        if approve:
            done = trust.approve_family_member(
                grant_ref=grant_id,
                approver_public_id=auth.actor_public_id,
                decision=decision,
                organization_public_id=org,
                verified=body.get("verified") is True,
                proof_kind=text(body, "proof_kind", False),
                proof_ref=text(body, "proof_ref", False),
                scopes=[s for s in body.get("scopes", []) if isinstance(s, str)]
                if isinstance(body.get("scopes"), list)
                else None,
                valid_until=text(body, "valid_until", False),
            )
        else:
            done = trust.reject_family_member(
                grant_ref=grant_id,
                approver_public_id=auth.actor_public_id,
                decision=decision,
                organization_public_id=org,
            )
    return JsonResponse({"request": _family_json(done)})


@endpoint("POST")
def staff_family_approve(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str
) -> HttpResponse:
    return _decide_family(request, auth, grant_id, approve=True)


def _family_step(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str, step: str
) -> HttpResponse:
    """A staff step on a guardian request or link: ask for proof, mark it in, pause, resume."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    body = json_body(request)
    with tenant_transaction():
        grant = trust.get_family_grant(grant_id)
    decision = _authorize(
        auth, "write:person", organization_public_id=org, subject_ref=grant["person_ref"]
    )
    me = auth.actor_public_id
    with tenant_transaction():
        if step == "proof-needed":
            done = trust.ask_for_proof(
                grant_ref=grant_id,
                approver_public_id=me,
                decision=decision,
                organization_public_id=org,
            )
        elif step == "proof-received":
            done = trust.mark_proof_received(
                grant_ref=grant_id,
                approver_public_id=me,
                decision=decision,
                organization_public_id=org,
            )
        elif step == "review":  # DEV-352: the yearly review of an adult's attorney or deputy
            done = trust.review_adult_grant(
                grant_ref=grant_id,
                approver_public_id=me,
                decision=decision,
                organization_public_id=org,
                still_in_force=body.get("still_in_force"),
            )
        elif step == "suspend":
            done = trust.suspend_family_member(
                grant_ref=grant_id,
                approver_public_id=me,
                decision=decision,
                organization_public_id=org,
                reason=text(body, "reason", False),
            )
        else:
            done = trust.resume_family_member(
                grant_ref=grant_id,
                approver_public_id=me,
                decision=decision,
                organization_public_id=org,
            )
    return JsonResponse({"request": _family_json(done)})


@endpoint("POST")
def staff_family_proof_needed(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str
) -> HttpResponse:
    return _family_step(request, auth, grant_id, "proof-needed")


@endpoint("POST")
def staff_family_proof_received(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str
) -> HttpResponse:
    return _family_step(request, auth, grant_id, "proof-received")


@endpoint("POST")
def staff_family_review(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str
) -> HttpResponse:
    return _family_step(request, auth, grant_id, "review")


@endpoint("POST")
def staff_family_suspend(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str
) -> HttpResponse:
    return _family_step(request, auth, grant_id, "suspend")


@endpoint("POST")
def staff_family_resume(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str
) -> HttpResponse:
    return _family_step(request, auth, grant_id, "resume")


@endpoint("POST")
def staff_family_reject(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str
) -> HttpResponse:
    return _decide_family(request, auth, grant_id, approve=False)


# ------------------------------------------------------------------ patient details (encrypted)
@endpoint("GET")
def my_profiles(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The details (name, birth date, phone) of every patient this login may read, decrypted
    only for people the policy engine allows."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:person", subject_ref=p)]
    with tenant_transaction():
        found = trust.get_person_profiles(persons)
    editable = {p for p in persons if is_allowed(auth, "write:person", subject_ref=p)}
    with tenant_transaction():
        own = trust.own_person_of(auth.actor_public_id)
        orgs = trust.organization_names()
        identity = {p: trust.identity_status_of(p) for p in persons}
    return JsonResponse(
        {
            "profiles": [
                {
                    "person": p,
                    "own": p == own,
                    "editable": p in editable,
                    "name": found.get(p, {}).get("name", ""),
                    "birth_date": found.get(p, {}).get("birth_date", ""),
                    "phone": found.get(p, {}).get("phone", ""),
                    # Whether a clinic has checked who this patient is. Never whether a
                    # possible duplicate is being reviewed: a patient is not told while it is
                    # unresolved (GHE-06).
                    "identity": {
                        "status": identity[p]["status"],
                        "checked_on": identity[p]["checked_on"],
                        "valid_until": identity[p]["valid_until"],
                        "clinic": orgs.get(identity[p]["clinic_ref"], ""),
                        "why_not": identity[p]["why_not"],
                    },
                }
                for p in persons
            ]
        }
    )


@endpoint("GET", "POST")
def person_profile(request: HttpRequest, auth: trust.Authenticated, person_id: str) -> HttpResponse:
    """GET: one patient's details, if this login may read that patient. POST: save them (the
    patient themselves, or clinic staff for their patient)."""
    org = _catalog_org(auth)
    if request.method == "GET":
        _authorize(auth, "read:person", organization_public_id=org, subject_ref=person_id)
        with tenant_transaction():
            found = trust.get_person_profiles([person_id]).get(person_id, {})
        return JsonResponse(
            {
                "profile": {
                    "person": person_id,
                    "name": found.get("name", ""),
                    "birth_date": found.get("birth_date", ""),
                    "phone": found.get("phone", ""),
                }
            }
        )
    body = json_body(request)
    decision = _authorize(auth, "write:person", organization_public_id=org, subject_ref=person_id)
    with tenant_transaction():
        saved = trust.set_person_profile(
            person_id,
            legal_name=text(body, "name"),
            birth_date=text(body, "birth_date", False),
            phone=text(body, "phone", False),
            actor_public_id=auth.actor_public_id,
            decision=decision,
            organization_public_id=org,
        )
    return JsonResponse(
        {
            "profile": {
                "person": person_id,
                "name": saved["legal_name"],
                "birth_date": saved["birth_date"],
                "phone": saved["phone"],
            }
        }
    )


# ------------------------------------------------------------------ insurance coverage
def _coverage_json(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c["public_id"],
        "person": c["person_ref"],
        "insurer": c["insurer_name"],
        "plan": c["plan_name"],
        "member_last4": c["member_last4"],
        "valid_from": c["valid_from"],
        "valid_until": c["valid_until"],
        "status": c["status"],
        "in_force": c["in_force"],
        "added_at": c["created_at"],
    }


@endpoint("GET", "POST")
def coverage_entries(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the insurance of every patient this login may read. POST: record a new coverage."""
    org = _catalog_org(auth)
    if request.method == "POST":
        body = json_body(request)
        with tenant_transaction():
            person = text(body, "person", required=False)
            if not person:
                mine = trust.reachable_persons(auth.actor_public_id)
                if len(mine) != 1:
                    raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["person"])
                person = mine[0]
        decision = _authorize(
            auth, "write:coverage", organization_public_id=org, subject_ref=person
        )
        with tenant_transaction():
            made = coverage.add_coverage(
                person_ref=person,
                insurer_name=text(body, "insurer"),
                plan_name=text(body, "plan", False),
                member_id=text(body, "member_id"),
                valid_from=text(body, "valid_from", False),
                valid_until=text(body, "valid_until", False),
                actor_public_id=auth.actor_public_id,
                decision=decision,
                organization_public_id=org,
            )
        return JsonResponse({"coverage": _coverage_json(made)}, status=201)
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:coverage", subject_ref=p)]
    pg = _page_req(request, auth, "coverage_mine")
    with tenant_transaction():
        rows = coverage.list_coverage(persons, page=pg)
    return JsonResponse(_with_cursor({"coverage": [_coverage_json(c) for c in rows]}, pg))


@endpoint("GET")
def person_coverage(
    request: HttpRequest, auth: trust.Authenticated, person_id: str
) -> HttpResponse:
    """One patient's insurance. The patient (or someone acting for them) always sees their own.
    Clinic staff see it only if the patient has chosen to share it (`shared` says which)."""
    org = _catalog_org(auth)
    _authorize(auth, "read:coverage", organization_public_id=org, subject_ref=person_id)
    with tenant_transaction():
        own = person_id in trust.reachable_persons(auth.actor_public_id)
        shared = own or coverage.shared_with_staff([person_id])[person_id]
        rows = (
            coverage.list_coverage([person_id], include_ended=bool(request.GET.get("ended")))
            if shared
            else []
        )
    return JsonResponse({"coverage": [_coverage_json(c) for c in rows], "shared": shared})


@endpoint("GET", "POST")
def coverage_sharing(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: for each patient this login may manage, whether staff may see their insurance.
    POST {person?, share}: the patient (or someone acting for them) chooses. Default: not shared."""
    org = _catalog_org(auth)
    if request.method == "POST":
        body = json_body(request)
        share = body.get("share")
        if not isinstance(share, bool):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["share"])
        with tenant_transaction():
            person = text(body, "person", required=False)
            if not person:
                mine = trust.reachable_persons(auth.actor_public_id)
                if len(mine) != 1:
                    raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["person"])
                person = mine[0]
        with tenant_transaction():
            reachable = trust.reachable_persons(auth.actor_public_id)
        if person not in reachable:  # staff may record insurance, never decide who sees it
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        decision = _authorize(
            auth, "write:coverage", organization_public_id=org, subject_ref=person
        )
        with tenant_transaction():
            done = coverage.set_sharing(
                person_ref=person,
                share=share,
                actor_public_id=auth.actor_public_id,
                decision=decision,
                organization_public_id=org,
            )
        return JsonResponse({"sharing": done})
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:coverage", subject_ref=p)]
    with tenant_transaction():
        chosen = coverage.shared_with_staff(persons)
    return JsonResponse(
        {"sharing": [{"person": p, "share_with_staff": chosen[p]} for p in persons]}
    )


@endpoint("POST")
def coverage_end(request: HttpRequest, auth: trust.Authenticated, coverage_id: str) -> HttpResponse:
    org = _catalog_org(auth)
    with tenant_transaction():
        current = coverage.get_coverage(coverage_id)
    decision = _authorize(
        auth, "write:coverage", organization_public_id=org, subject_ref=current["person_ref"]
    )
    with tenant_transaction():
        done = coverage.end_coverage(
            coverage_ref=coverage_id,
            actor_public_id=auth.actor_public_id,
            decision=decision,
            organization_public_id=org,
        )
        person = current["person_ref"]
        own = person in trust.reachable_persons(auth.actor_public_id)
        shared = own or coverage.shared_with_staff([person])[person]
    if not shared:  # staff may remove it, but may not read it: no details in the answer
        return JsonResponse({"coverage": {"id": done["public_id"], "status": done["status"]}})
    return JsonResponse({"coverage": _coverage_json(done)})


# ------------------------------------------------------------------ waiting list
def _waitlist_json(
    rows: list[dict[str, Any]], auth: trust.Authenticated, *, staff: bool = False
) -> list[dict[str, Any]]:
    """A patient never sees where the clinic ranks them: priority is for staff screens only."""
    with tenant_transaction():
        orgs = trust.organization_names()
        cache: dict[str, Any] = {}
        out = []
        for e in rows:
            service = cache.setdefault(e["service_ref"], catalog.get_service(e["service_ref"]))
            out.append(
                {
                    "id": e["public_id"],
                    "state": e["state"],
                    "patient": e["person_ref"],
                    "joined_at": e["created_at"],
                    "offered_at": e["offered_at"],
                    "service": {"id": service["public_id"], "name": service["name"]},
                    "organization": {
                        "id": e["organization_ref"],
                        "name": orgs.get(e["organization_ref"], ""),
                    },
                    **(
                        {"priority": e["priority"], "priority_reason": e["priority_reason"]}
                        if staff
                        else {}
                    ),
                }
            )
    return _add_names(auth, out, "patient")


@endpoint("GET", "POST")
def waitlist_entries(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the waiting lists a patient (or guardian) is on. POST: join one."""
    if request.method == "POST":
        body = json_body(request)
        service_ref = text(body, "service")
        with tenant_transaction():
            person = text(body, "person", required=False)
            if not person:
                mine = trust.reachable_persons(auth.actor_public_id)
                if len(mine) != 1:
                    raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["person"])
                person = mine[0]
            service = catalog.get_service(service_ref)
        decision = _authorize(
            auth,
            "write:booking",
            organization_public_id=service["organization_ref"],
            subject_ref=person,
        )
        with tenant_transaction():
            made = waitlist.join(
                person_ref=person,
                service_ref=service_ref,
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"entry": _waitlist_json([made], auth)[0]}, status=201)
    pg = _page_req(request, auth, "waitlist.entries")
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:booking", subject_ref=p)]
    rows: list[dict[str, Any]] = []
    if persons:
        with tenant_transaction():
            rows = waitlist.list_entries(person_refs=persons, page=pg)
    return JsonResponse(_with_cursor({"entries": _waitlist_json(rows, auth)}, pg))


@endpoint("GET")
def staff_waitlist(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Everyone waiting for this organization's services, in queue order, with their place."""
    _staff_only(auth)
    rows: list[dict[str, Any]] = []
    for org in held_organizations(auth):
        if is_allowed(auth, "read:booking", organization_public_id=org):
            with tenant_transaction():
                rows += waitlist.list_entries(organization_ref=org)
    # The order times are offered in (DEV-366): most urgent first, then longest waiting.
    rows.sort(key=lambda e: (-e["priority"], e["created_at"]))
    place: dict[str, int] = {}
    entries = _waitlist_json(rows, auth, staff=True)
    for item in entries:
        place[item["service"]["id"]] = place.get(item["service"]["id"], 0) + 1
        item["place"] = place[item["service"]["id"]]
    return JsonResponse({"entries": entries})


@endpoint("POST")
def staff_waitlist_priority(
    request: HttpRequest, auth: trust.Authenticated, entry_id: str
) -> HttpResponse:
    """Clinic staff set how soon someone is offered a time (DEV-366):
    {"priority": 0|1|2, "reason": a PriorityReason code, needed above 0}. Staff only: a patient or
    guardian cannot rank themselves."""
    _staff_only(auth)
    body = json_body(request)
    with tenant_transaction():
        entry = waitlist.get_entry(entry_id)
    decision = _authorize(
        auth, "write:booking", organization_public_id=entry["organization_ref"],
        subject_ref=entry["person_ref"])
    with tenant_transaction():
        done = waitlist.set_priority(
            entry_ref=entry_id, priority=body.get("priority"), reason=body.get("reason", ""),
            actor_public_id=auth.actor_public_id, decision=decision)
    return JsonResponse({"entry": _waitlist_json([done], auth, staff=True)[0]})


@endpoint("POST")
def waitlist_leave(request: HttpRequest, auth: trust.Authenticated, entry_id: str) -> HttpResponse:
    with tenant_transaction():
        entry = waitlist.get_entry(entry_id)
    decision = _authorize(
        auth,
        "write:booking",
        organization_public_id=entry["organization_ref"],
        subject_ref=entry["person_ref"],
    )
    with tenant_transaction():
        done = waitlist.leave(
            entry_ref=entry_id, actor_public_id=auth.actor_public_id, decision=decision
        )
    return JsonResponse({"entry": _waitlist_json([done], auth)[0]})


# ------------------------------------------------------------------ referrals
def _referral_json(
    r: dict[str, Any],
    services: dict[str, Any],
    orgs: dict[str, str],
    labels: dict[str, str],
) -> dict[str, Any]:
    service = services.setdefault(r["service_ref"], catalog.get_service(r["service_ref"]))
    dest = r["destination_organization_ref"]
    return {
        "id": r["public_id"],
        "patient": r["person_ref"],
        "state": r["state"],
        "urgency": r["urgency"],
        "reason": r["reason"],
        "decision_reason": r["decision_reason"],
        "decided_at": r["decided_at"],
        "created_at": r["created_at"],
        "acknowledged_at": r["acknowledged_at"],
        "escalated": r["escalated"],
        "owner": (
            {"id": r["owner_ref"], "name": labels.get(r["owner_ref"], "")}
            if r["owner_ref"]
            else None
        ),
        "responsible": {"id": r["responsible_ref"], "name": labels.get(r["responsible_ref"], "")},
        "referrer": {"id": r["referrer_ref"], "name": labels.get(r["referrer_ref"], "")},
        "from_organization": {
            "id": r["from_organization_ref"],
            "name": orgs.get(r["from_organization_ref"], ""),
        },
        "service": {"id": service["public_id"], "name": service["name"]},
        "organization": {"id": dest, "name": orgs.get(dest, "")},
    }


def _referrals_out(rows: list[dict[str, Any]], auth: trust.Authenticated) -> list[dict[str, Any]]:
    with tenant_transaction():
        orgs, labels = trust.organization_names(), trust.actor_labels()
        cache: dict[str, Any] = {}
        out = [_referral_json(r, cache, orgs, labels) for r in rows]
    return _add_names(auth, out, "patient")


@endpoint("GET", "POST")
def clinician_referrals(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """A doctor sends a patient to another service, and sees what became of their referrals."""
    _clinician_only(auth)
    if request.method == "GET":
        pg = _page_req(request, auth, "referrals_sent")
        with tenant_transaction():
            rows = referral.list_referrals(referrer_ref=auth.actor_public_id, page=pg)
        allowed = {
            p
            for p in sorted({r["person_ref"] for r in rows})
            if is_allowed(auth, "read:referral", subject_ref=p)
        }
        return JsonResponse(_with_cursor(
            {"referrals": _referrals_out([r for r in rows if r["person_ref"] in allowed], auth)},
            pg))
    body = json_body(request)
    person, service = text(body, "patient"), text(body, "service")
    org = _catalog_org(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    decision = _authorize(auth, "write:referral", organization_public_id=org, subject_ref=person)
    with tenant_transaction():
        made = referral.issue(
            person_ref=person,
            service_ref=service,
            reason=text(body, "reason"),
            urgency=text(body, "urgency", required=False) or "routine",
            actor_public_id=auth.actor_public_id,
            from_organization_ref=org,
            decision=decision,
        )
    return JsonResponse({"referral": _referrals_out([made], auth)[0]}, status=201)


@endpoint("GET")
def referrals_incoming(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Referrals sent TO this organization's services, for its operations staff to decide."""
    _staff_only(auth)
    seen: dict[str, dict[str, Any]] = {}
    for org in held_organizations(auth):
        if is_allowed(auth, "read:referral", organization_public_id=org):
            with tenant_transaction():
                for r in referral.list_referrals(destination_organization_ref=org):
                    seen[r["public_id"]] = r
    return JsonResponse({"referrals": _referrals_out(list(seen.values()), auth)})


@endpoint("POST")
def referral_decide(
    request: HttpRequest, auth: trust.Authenticated, referral_id: str
) -> HttpResponse:
    """Accept, ask for more, or decline. Only the named owner of the receiving inbox, or the
    fallback, who is a doctor (the domain re-checks all of this)."""
    _clinician_only(auth)
    body = json_body(request)
    with tenant_transaction():
        current = referral.get_referral(referral_id)
    decision = _authorize(
        auth,
        "write:referral",
        organization_public_id=current["destination_organization_ref"],
        subject_ref=current["person_ref"],
    )
    with tenant_transaction():
        done = referral.decide(
            referral_ref=referral_id,
            outcome=text(body, "decision"),
            reason=text(body, "reason", required=False),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"referral": _referrals_out([done], auth)[0]})


@endpoint("POST")
def referral_acknowledge(
    request: HttpRequest, auth: trust.Authenticated, referral_id: str
) -> HttpResponse:
    """Receiving staff mark a referral as received. This does not accept care."""
    _staff_only(auth)
    with tenant_transaction():
        current = referral.get_referral(referral_id)
    decision = _authorize(
        auth,
        "write:referral",
        organization_public_id=current["destination_organization_ref"],
        subject_ref=current["person_ref"],
    )
    with tenant_transaction():
        done = referral.acknowledge(
            referral_ref=referral_id, actor_public_id=auth.actor_public_id, decision=decision
        )
    return JsonResponse({"referral": _referrals_out([done], auth)[0]})


@endpoint("GET")
def clinician_referral_inbox(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Referrals this doctor is named to answer, as owner or as the fallback."""
    _clinician_only(auth)
    pg = _page_req(request, auth, "referrals_to_answer")
    with tenant_transaction():
        rows = referral.list_for_answerer(auth.actor_public_id, page=pg)
    allowed = {
        p
        for p in sorted({r["person_ref"] for r in rows})
        if is_allowed(auth, "read:referral", subject_ref=p)
    }
    return JsonResponse(_with_cursor(
        {"referrals": _referrals_out([r for r in rows if r["person_ref"] in allowed], auth)}, pg))


# ------------------------------------------------------------------ data-rights requests (DEV-126)
def _rights_json(c: dict[str, Any], staff: bool = False) -> dict[str, Any]:
    out = {
        "id": c["public_id"],
        "ref": c["ref_no"],
        "type": c["request_type"],
        "state": c["state"],
        "note": c["note"],
        "created_at": c["created_at"],
        "identity_checked_at": c["identity_checked_at"],
        "decision_note": c["decision_note"],
        "decided_at": c["decided_at"],
        "patient": c["person_ref"],
        "due_at": c["due_at"],
        "due_basis": c["due_basis"],
        # The patient is told only THAT some data is kept for a legal reason; why is for staff.
        "lawful_hold": c["lawful_hold"],
    }
    if staff:
        out["hold_reason"] = c["hold_reason"]
    return out


def _enabled_capabilities(auth: trust.Authenticated) -> frozenset[str]:
    """What the signed country packs for this customer's country switch on (none if no country or
    no pack). Read in the platform tenant, outside the customer's own transaction."""
    return jurisdiction.customer_capabilities(auth.context.tenant_id)


def _data_request_deadline(auth: trust.Authenticated) -> tuple[int, str] | None:
    """The deadline (days, pack) a signed country pack gives this customer's data requests, or None.
    Packs live in Zoiko's platform tenant, so this reads them there, OUTSIDE the customer's own
    transaction (switching tenants inside one would lose the first context)."""
    country = trust.tenant_country(auth.context.tenant_id)
    platform = next(
        (t for t in trust.list_tenants() if t["type"] == "platform" and t["status"] == "active"),
        None,
    )
    if not country or platform is None:
        return None
    with tenant_transaction(trust.context_for(platform["public_id"])):
        return jurisdiction.data_request_days(country)


@endpoint("GET")
def my_emergency(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The local emergency number for this patient's country, only when a signed, current country
    pack states one (DEV-185). Otherwise `number` is null and the app says nothing more than its
    general wording: a number is never invented or guessed. Patients only."""
    if home_for(auth.actor_type, list(auth.roles), auth.tenant_type) != "patient":
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    country = trust.tenant_country(auth.context.tenant_id)
    platform = next(
        (t for t in trust.list_tenants() if t["type"] == "platform" and t["status"] == "active"),
        None,
    )
    found = None
    if country and platform is not None:
        with tenant_transaction(trust.context_for(platform["public_id"])):
            found = jurisdiction.emergency_number(country)
    return JsonResponse({"number": found[0] if found else None})


@endpoint("GET", "POST")
def rights_requests(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: this patient's own data-rights requests. POST {type, note?}: ask their clinic.
    Only the patient themselves (a guardian's request needs proof of authority we do not hold)."""
    org = _catalog_org(auth)
    with tenant_transaction():
        person = trust.own_person_of(auth.actor_public_id)
    if not person or org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:rights", organization_public_id=org, subject_ref=person)
        deadline = _data_request_deadline(auth)
        with tenant_transaction():
            made = rights.file_request(
                person_ref=person,
                organization_ref=org,
                request_type=text(body, "type"),
                note=text(body, "note", required=False),
                actor_public_id=auth.actor_public_id,
                decision=decision,
                deadline=deadline,
            )
        return JsonResponse({"request": _rights_json(made)}, status=201)
    _authorize(auth, "read:rights", organization_public_id=org, subject_ref=person)
    with tenant_transaction():
        rows = rights.list_cases(person_refs=[person])
    return JsonResponse({"requests": [_rights_json(c) for c in rows]})


@endpoint("POST")
def rights_withdraw(request: HttpRequest, auth: trust.Authenticated, case_id: str) -> HttpResponse:
    with tenant_transaction():
        current = rights.get_case(case_id)
    decision = _authorize(
        auth,
        "write:rights",
        organization_public_id=current["organization_ref"],
        subject_ref=current["person_ref"],
    )
    with tenant_transaction():
        done = rights.withdraw(
            case_ref=case_id, actor_public_id=auth.actor_public_id, decision=decision
        )
    return JsonResponse({"request": _rights_json(done)})


@endpoint("GET")
def staff_rights(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Data-rights requests sent to this clinic, newest first. The patient's own words are shown to
    the clinic staff who must act on them."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:rights", organization_public_id=org)
    with tenant_transaction():
        rows = rights.list_cases(organization_ref=org)
    out = [_rights_json(c, staff=True) for c in rows]
    _add_names(auth, out, "patient")
    return JsonResponse({"requests": out, "can_close": "R13" in auth.roles})


@endpoint("POST")
def staff_rights_step(
    request: HttpRequest, auth: trust.Authenticated, case_id: str, step: str
) -> HttpResponse:
    """A staff step: start, verify (the identity check), complete or decline (administrator)."""
    _staff_only(auth)
    if step not in ("start", "verify", "complete", "decline", "hold", "release"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    body = json_body(request)
    with tenant_transaction():
        current = rights.get_case(case_id)
    decision = _authorize(
        auth,
        "write:rights",
        organization_public_id=current["organization_ref"],
        subject_ref=current["person_ref"],
    )
    me = auth.actor_public_id
    with tenant_transaction():
        if step == "start":
            done = rights.start(case_ref=case_id, actor_public_id=me, decision=decision)
        elif step == "verify":
            done = rights.confirm_identity(case_ref=case_id, actor_public_id=me, decision=decision)
        elif step == "hold":
            done = rights.place_hold(
                case_ref=case_id,
                reason=text(body, "note", required=False),
                actor_public_id=me,
                decision=decision,
            )
        elif step == "release":
            done = rights.lift_hold(
                case_ref=case_id,
                note=text(body, "note", required=False),
                actor_public_id=me,
                decision=decision,
            )
        else:
            done = rights.close(
                case_ref=case_id,
                outcome="completed" if step == "complete" else "declined",
                note=text(body, "note", required=False),
                actor_public_id=me,
                decision=decision,
            )
    return JsonResponse({"request": _rights_json(done, staff=True)})


# ------------------------------------------------------------------ workflow engine (DEV-132)
def _owners(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = trust.actor_labels()
    for r in rows:
        r["owner_name"] = labels.get(r["owner"], "")
    return rows


@endpoint("GET", "POST")
def staff_workflow_definitions(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the workflow definitions. POST {key, journey, path, timer_policies}: a draft (admin)."""
    org = _staff_only(auth)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:workflow", organization_public_id=org)
        with tenant_transaction():
            made = orchestration.create_definition(
                key=text(body, "key"),
                journey=text(body, "journey"),
                path=body.get("path"),
                timer_policies=body.get("timer_policies"),
                actor_public_id=auth.actor_public_id,
                decision=decision,
                organization_ref=org,
                required_capabilities=body.get("required_capabilities"),
            )
        return JsonResponse({"definition": made}, status=201)
    _authorize(auth, "read:workflow", organization_public_id=org)
    with tenant_transaction():
        rows = orchestration.list_definitions()
    return JsonResponse({"definitions": rows, "can_write": "R13" in auth.roles})


@endpoint("POST")
def staff_workflow_definition_step(
    request: HttpRequest, auth: trust.Authenticated, definition_id: str, step: str
) -> HttpResponse:
    """edit | publish | version | retire."""
    org = _staff_only(auth)
    if step not in ("edit", "publish", "version", "retire"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    body = json_body(request)
    decision = _authorize(auth, "write:workflow", organization_public_id=org)
    me = auth.actor_public_id
    with tenant_transaction():
        if step == "edit":
            done = orchestration.edit_definition(
                definition_ref=definition_id,
                path=body.get("path"),
                timer_policies=body.get("timer_policies"),
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
                required_capabilities=body.get("required_capabilities"),
            )
        else:
            action = {
                "publish": orchestration.publish_definition,
                "version": orchestration.new_version,
                "retire": orchestration.retire_definition,
            }[step]
            done = action(
                definition_ref=definition_id,
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
    return JsonResponse({"definition": done})


@endpoint("GET", "POST")
def staff_workflows(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: running workflows (?view=workbench for the ones needing a person). POST: start one."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:workflow", organization_public_id=org)
        enabled = _enabled_capabilities(auth)
        with tenant_transaction():
            made = orchestration.start(
                definition_key=text(body, "definition"),
                subject_ref=text(body, "subject"),
                organization_ref=org,
                owner_ref=text(body, "owner"),
                evidence=body.get("evidence"),
                purpose=text(body, "purpose", required=False),
                idempotency_key=text(body, "idempotency_key", required=False),
                actor_public_id=auth.actor_public_id,
                decision=decision,
                enabled_capabilities=enabled,
            )
            _owners([made])
        return JsonResponse({"instance": made}, status=200 if made["replayed"] else 201)
    _authorize(auth, "read:workflow", organization_public_id=org)
    with tenant_transaction():
        rows = _owners(
            orchestration.list_instances(
                org,
                workbench=request.GET.get("view") == "workbench",
                state=request.GET.get("state") or None,
            )
        )
    return JsonResponse({"instances": rows})


@endpoint("GET")
def staff_workflow_history(
    request: HttpRequest, auth: trust.Authenticated, instance_id: str
) -> HttpResponse:
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:workflow", organization_public_id=org)
    with tenant_transaction():
        out = orchestration.history(instance_id, org)
        labels = trust.actor_labels()
    out["instance"]["owner_name"] = labels.get(out["instance"]["owner"], "")
    for s in out["steps"]:
        s["actor_name"] = labels.get(s["actor"], s["actor"])
    return JsonResponse(out)


@endpoint("POST")
def staff_workflow_step(
    request: HttpRequest, auth: trust.Authenticated, instance_id: str, step: str
) -> HttpResponse:
    """advance | resume | overlay | reassign. Every one names the version it expects."""
    org = _staff_only(auth)
    if org is None or step not in ("advance", "resume", "overlay", "reassign"):
        raise ZoikoError(ErrorCode.NOT_FOUND if org else ErrorCode.POLICY_DENIED)
    body = json_body(request)
    version = body.get("expected_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["expected_version"])
    decision = _authorize(auth, "write:workflow", organization_public_id=org)
    me = auth.actor_public_id
    note = text(body, "note", required=False)
    enabled = _enabled_capabilities(auth) if step == "advance" else frozenset()
    with tenant_transaction():
        if step == "advance":
            done = orchestration.advance(
                instance_ref=instance_id,
                organization_ref=org,
                expected_version=version,
                to_state=text(body, "to_state"),
                evidence=body.get("evidence"),
                idempotency_key=text(body, "idempotency_key"),
                purpose=text(body, "purpose", required=False),
                note=note,
                actor_public_id=me,
                decision=decision,
                incident_owner_ref=text(body, "incident_owner", required=False),
                enabled_capabilities=enabled,
            )
        elif step == "resume":
            done = orchestration.resume(
                instance_ref=instance_id,
                organization_ref=org,
                expected_version=version,
                evidence=body.get("evidence"),
                note=note,
                actor_public_id=me,
                decision=decision,
            )
        elif step == "overlay":
            done = orchestration.set_overlay(
                instance_ref=instance_id,
                organization_ref=org,
                expected_version=version,
                overlay=text(body, "overlay"),
                note=note,
                actor_public_id=me,
                decision=decision,
                evidence=body.get("evidence"),
            )
        else:
            done = orchestration.reassign(
                instance_ref=instance_id,
                organization_ref=org,
                expected_version=version,
                new_owner_ref=text(body, "owner"),
                actor_public_id=me,
                decision=decision,
            )
        _owners([done])
    return JsonResponse({"instance": done})


@endpoint("GET")
def staff_workflow_escalations(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:workflow", organization_public_id=org)
    with tenant_transaction():
        rows = orchestration.list_escalations(org, open_only=request.GET.get("all") != "1")
    return JsonResponse({"escalations": rows})


@endpoint("POST")
def staff_workflow_escalation_record(
    request: HttpRequest, auth: trust.Authenticated, escalation_id: str
) -> HttpResponse:
    """A person says whether a human was reached. {reached, note, fallback_owner?}"""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    body = json_body(request)
    if not isinstance(body.get("reached"), bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["reached"])
    decision = _authorize(auth, "write:workflow", organization_public_id=org)
    with tenant_transaction():
        done = orchestration.record_escalation(
            escalation_ref=escalation_id,
            organization_ref=org,
            reached=body["reached"],
            note=text(body, "note", required=False),
            fallback_owner_ref=text(body, "fallback_owner", required=False),
            actor_public_id=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"escalation": done})


# ------------------------------------------------------------------ outbound webhooks (DEV-127)
def _manager(auth: trust.Authenticated) -> str | None:
    """Only a clinic administrator or a partner developer (R10) may use these; returns the
    organization."""
    org = _staff_only(auth, "developer")
    if not {"R13", "R10"} & set(auth.roles):
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return org


@endpoint("GET", "POST")
def staff_webhooks(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the registered addresses. POST {label, url, topics}: add one (secret shown once)."""
    org = _manager(auth)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:integration", organization_public_id=org)
        topics = body.get("topics")
        with tenant_transaction():
            made = integration.create_endpoint(
                label=text(body, "label"),
                url=text(body, "url"),
                topics=topics if isinstance(topics, list) else [],
                organization_ref=org,
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"endpoint": made}, status=201)
    _authorize(auth, "read:integration", organization_public_id=org)
    with tenant_transaction():
        rows = integration.list_endpoints()
    return JsonResponse({"endpoints": rows})


@endpoint("POST")
def staff_webhook_step(
    request: HttpRequest, auth: trust.Authenticated, endpoint_id: str, step: str
) -> HttpResponse:
    """verify | pause | resume | revoke | rotate-secret."""
    org = _manager(auth)
    if step not in ("verify", "pause", "resume", "revoke", "rotate-secret"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    decision = _authorize(auth, "write:integration", organization_public_id=org)
    me = auth.actor_public_id
    if step == "verify":
        # the network call must not hold a database transaction open longer than it has to
        with tenant_transaction():
            done = integration.verify_endpoint(
                endpoint_ref=endpoint_id,
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
    elif step == "rotate-secret":
        with tenant_transaction():
            done = integration.rotate_secret(
                endpoint_ref=endpoint_id,
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
    else:
        with tenant_transaction():
            done = integration.set_state(
                endpoint_ref=endpoint_id,
                step=step,
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
    return JsonResponse({"endpoint": done})


@endpoint("GET")
def staff_webhook_deliveries(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _manager(auth)
    _authorize(auth, "read:integration", organization_public_id=org)
    pg = _page_req(request, auth, f"deliveries:{request.GET.get('state') or ''}")
    with tenant_transaction():
        rows = integration.list_deliveries(request.GET.get("state") or None, page=pg)
        labels = trust.actor_labels()
    for r in rows:
        r["owner_name"] = labels.get(r["owner"], "")
    return JsonResponse(_with_cursor({"deliveries": rows}, pg))


@endpoint("GET", "POST")
def staff_partners(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: partners allowed to send to us. POST {label, allowed_types}: add one (secret once)."""
    org = _manager(auth)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:integration", organization_public_id=org)
        with tenant_transaction():
            made = integration.create_client(
                label=text(body, "label"),
                allowed_types=body.get("allowed_types"),
                organization_ref=org,
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"client": made}, status=201)
    _authorize(auth, "read:integration", organization_public_id=org)
    with tenant_transaction():
        rows = integration.list_clients()
    return JsonResponse({"clients": rows})


@endpoint("POST")
def staff_partner_step(
    request: HttpRequest, auth: trust.Authenticated, client_id: str, step: str
) -> HttpResponse:
    """suspend | resume | revoke | rotate-secret | issue-secret (after Zoiko's three checks)."""
    org = _manager(auth)
    if step not in ("suspend", "resume", "revoke", "rotate-secret", "issue-secret"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    decision = _authorize(auth, "write:integration", organization_public_id=org)
    me = auth.actor_public_id
    with tenant_transaction():
        if step == "rotate-secret":
            done = integration.rotate_client_secret(
                client_ref=client_id, actor_public_id=me, decision=decision, organization_ref=org
            )
        elif step == "issue-secret":
            done = integration.issue_client_secret(
                client_ref=client_id, actor_public_id=me, decision=decision, organization_ref=org
            )
        else:
            done = integration.set_client_state(
                client_ref=client_id,
                step=step,
                actor_public_id=me,
                decision=decision,
                organization_ref=org,
            )
    return JsonResponse({"client": done})


@endpoint("GET")
def platform_partner_checks(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Partners of customers that still wait for Zoiko's identity, legal and security checks."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    return JsonResponse({"partners": integration.pending_for_platform()})


@endpoint("POST")
def platform_partner_check(
    request: HttpRequest, auth: trust.Authenticated, customer_id: str, client_id: str, check: str
) -> HttpResponse:
    """A support person records one check (identity | legal | security) with a note."""
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    done = integration.record_check(
        customer_ref=customer_id,
        client_ref=client_id,
        check=check,
        note=body.get("note"),
        actor_public_id=auth.actor_public_id,
        decision=decision,
        platform_organization_ref=org,
    )
    return JsonResponse({"client": done})


@endpoint("GET")
def staff_inbound_events(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _manager(auth)
    _authorize(auth, "read:integration", organization_public_id=org)
    pg = _page_req(request, auth, f"inbound:{request.GET.get('state') or ''}")
    with tenant_transaction():
        rows = integration.list_events(request.GET.get("state") or None, page=pg)
    return JsonResponse(_with_cursor({"events": rows}, pg))


@csrf_exempt
@require_POST
def sendgrid_events(request: HttpRequest) -> HttpResponse:
    """SendGrid's delivery receipts (DEV-354). No sign-in: SendGrid's ECDSA signature IS the
    credential and is checked before the body is read. Only our own email id and the outcome
    are used; addresses and anything else in the post are ignored and never stored."""
    import datetime as _dt
    import time as _time

    from foundation import sendgrid_backend
    from foundation.mail import record_delivery

    body = request.read(1_000_001)
    if len(body) > 1_000_000:
        return HttpResponse(status=413)
    ok = sendgrid_backend.verify_event_signature(
        body, request.headers.get(sendgrid_backend.SIGNATURE_HEADER, ""),
        request.headers.get(sendgrid_backend.TIMESTAMP_HEADER, ""), _time.time())
    if not ok:
        return HttpResponse(status=401)
    try:
        events = json.loads(body)
    except ValueError:
        return HttpResponse(status=400)
    changed = 0
    for ref, status, when in sendgrid_backend.receipts_from(events):
        changed += record_delivery(
            ref, status, _dt.datetime.fromtimestamp(when, tz=_dt.timezone.utc))
    return JsonResponse({"recorded": changed})


@csrf_exempt
@require_POST
def twilio_status(request: HttpRequest) -> HttpResponse:
    """Twilio's delivery receipt for a text (DEV-199). No sign-in: Twilio's signature over the
    exact public address and the form fields is the credential, checked before anything is used.
    The address must be the one Twilio was given (TWILIO_STATUS_URL), not one rebuilt here."""
    from foundation.texts import record_receipt

    url = settings.TWILIO_STATUS_URL or request.build_absolute_uri()
    status = record_receipt(
        url=url, params=request.POST.dict(), signature=request.headers.get("X-Twilio-Signature", "")
    )
    return HttpResponse(status=status)


@csrf_exempt
@require_POST
def partner_events(request: HttpRequest) -> HttpResponse:
    """A partner's signed message (GHE-13 WF-08). No sign-in: the signature IS the credential, and
    it is checked before anything in the body is read. Every identity failure is the same 401."""
    status, body, headers = integration.receive(
        raw_body=request.body,
        signature=request.headers.get("Zoiko-Signature", ""),
        delivery_id=request.headers.get("Zoiko-Delivery-Id", ""),
    )
    _note_codes(request.body, status)
    response = JsonResponse(body, status=status)
    response["Cache-Control"] = "no-store"
    for name, value in headers.items():
        response[name] = value
    return response


def _note_codes(raw: bytes, status: int) -> None:
    """After a signed message was taken in (accepted or quarantined), list each local code it
    carried for two-person review (DEV-322). The message itself is not touched. A failure here is
    logged by class only and never changes the partner's answer."""
    if status not in (200, 201, 202):
        return
    try:
        codings = [
            c for c in fhir.codings_in(integration.parse_payload(raw.decode("utf-8")))
            # the standard rating codes (N, A, CRIT ...) are not a lab's own codes
            if c["system"] != "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
        ]
        if codings:
            with tenant_transaction(_platform_context()):
                codemap.note_seen(codings)
    except Exception as exc:  # noqa: BLE001 - listing codes must never break the intake
        logging.getLogger("zoiko.codemap").warning("noting codes failed: %s", type(exc).__name__)


# ------------------------------------------------------------------ code mapping review (DEV-322)
@endpoint("GET")
def platform_codemaps(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Local codes partners sent, and where each stands: needs review, proposed, approved,
    rejected."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        rows = codemap.list_mappings(state=request.GET.get("state") or None)
        names = trust.actor_labels()
    for r in rows:
        r["proposed_by_name"] = names.get(str(r["proposed_by"]), "") if r["proposed_by"] else ""
        r["decided_by_name"] = names.get(str(r["decided_by"]), "") if r["decided_by"] else ""
    return JsonResponse({"mappings": rows, "me": auth.actor_public_id,
                         "can_propose": "R11" in auth.roles, "can_decide": "R12" in auth.roles})


@endpoint("POST")
def platform_codemap_step(
    request: HttpRequest, auth: trust.Authenticated, mapping_id: str, step: str
) -> HttpResponse:
    """propose {target_code} (regional support, R11) | approve | reject {reason} (the clinical
    safety officer, R12). Nobody approves or rejects their own proposal."""
    org = _platform_only(auth)
    body = json_body(request)
    if step == "propose":
        if "R11" not in auth.roles:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        _authorize(auth, "write:platform", organization_public_id=org)
        with tenant_transaction():
            done = codemap.propose(
                mapping_ref=mapping_id, target_code=body.get("target_code"),
                by=auth.actor_public_id,
            )
    elif step in ("approve", "reject"):
        if "R12" not in auth.roles:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        _authorize(auth, "attest:platform", organization_public_id=org)
        with tenant_transaction():
            if step == "approve":
                done = codemap.approve(mapping_ref=mapping_id, by=auth.actor_public_id)
            else:
                done = codemap.reject(
                    mapping_ref=mapping_id, reason=body.get("reason"), by=auth.actor_public_id
                )
    else:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return JsonResponse({"mapping": done})


def _capped_body(request: HttpRequest) -> bytes:
    """A lab message's body, read with the intake's own cap (7.5 MB, so a 5 MB PDF fits) instead
    of Django's general 2.5 MB; anything longer is cut, and the intake then refuses it as too
    large (DEV-349)."""
    return request.read(7_500_001)


@csrf_exempt
@require_POST
def hl7_inbound(request: HttpRequest) -> HttpResponse:
    """A partner's signed HL7 v2 lab result (ORU^R01), DEV-340: read into the same FHIR shape and
    then treated exactly like a FHIR message (same signature, checks, holding and delivery)."""
    raw = _capped_body(request)
    status, body, headers = integration.receive_fhir(
        raw_body=raw,
        signature=request.headers.get("Zoiko-Signature", ""),
        delivery_id=request.headers.get("Zoiko-Delivery-Id", ""),
        fmt="hl7v2",
    )
    _note_codes(raw, status)  # local codes go to the two-person review list (DEV-322)
    response = JsonResponse(body, status=status)
    response["Cache-Control"] = "no-store"
    for name, value in headers.items():
        response[name] = value
    return response


@csrf_exempt
@require_POST
def fhir_inbound(request: HttpRequest) -> HttpResponse:
    """A partner's signed FHIR message (GHE-13 T-25, T-60; DEV-315, docs/FHIR-DESIGN.md).

    Signature proves transport origin. Valid resources are taken in (DiagnosticReport mapped to
    exchange delivery); invalid or unmappable resources are stored in quarantine with no clinical
    state.
    """
    raw = _capped_body(request)
    status, body, headers = integration.receive_fhir(
        raw_body=raw,
        signature=request.headers.get("Zoiko-Signature", ""),
        delivery_id=request.headers.get("Zoiko-Delivery-Id", ""),
    )
    _note_codes(raw, status)  # local codes go to the two-person review list (DEV-322)
    response = JsonResponse(body, status=status)
    response["Cache-Control"] = "no-store"
    for name, value in headers.items():
        response[name] = value
    return response


@endpoint("GET")
def staff_fhir_quarantine(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """List quarantined FHIR messages for clinic staff review (GHE-13 T-25, T-60)."""
    org = _manager(auth)
    _authorize(auth, "read:integration", organization_public_id=org)
    state = request.GET.get("state")
    pg = _page_req(request, auth, f"fhir_held:{state or ''}")
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id,
            action="integration.fhir_quarantine_listed",
            object_type="fhir_quarantine",
            object_ref="quarantine",
            outcome=evidence.AuditOutcome.ALLOWED,
        )
        items = integration.list_quarantined(state=state, page=pg)
    return JsonResponse(_with_cursor({"items": items}, pg))


@endpoint("POST")
def staff_fhir_quarantine_step(
    request: HttpRequest, auth: trust.Authenticated, quarantine_id: str, step: str
) -> HttpResponse:
    """Clinic staff discards, requests a resend for, or retries (DEV-338) a held FHIR message."""
    org = _manager(auth)
    decision = _authorize(auth, "write:integration", organization_public_id=org)
    body = json_body(request)
    if step == "retry":
        with tenant_transaction():
            done = integration.retry_quarantined(
                quarantine_ref=quarantine_id, actor_public_id=auth.actor_public_id,
                decision=decision, organization_ref=org)
        return JsonResponse({"item": done})
    with tenant_transaction():
        done = integration.quarantine_step(
            quarantine_ref=quarantine_id,
            step=step,
            actor_public_id=auth.actor_public_id,
            decision=decision,
            organization_ref=org,
            note=body.get("note"),
        )
    return JsonResponse({"item": done})


@endpoint("POST")
def staff_webhook_redrive(
    request: HttpRequest, auth: trust.Authenticated, delivery_id: str
) -> HttpResponse:
    org = _manager(auth)
    decision = _authorize(auth, "write:integration", organization_public_id=org)
    with tenant_transaction():
        done = integration.redrive(
            delivery_ref=delivery_id,
            actor_public_id=auth.actor_public_id,
            decision=decision,
            organization_ref=org,
        )
    return JsonResponse({"delivery": done})


@endpoint("GET", "POST")
def staff_referral_inbox(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Who answers referrals sent to this clinic site. GET: the current owner and fallback and
    the doctors who could be named. POST (clinic administrator only): name them."""
    _staff_only(auth)
    org = _catalog_org(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if request.method == "POST":
        body = json_body(request)
        decision = _authorize(auth, "write:referral", organization_public_id=org)
        with tenant_transaction():
            referral.set_inbox(
                organization_ref=org,
                owner_ref=text(body, "owner"),
                fallback_ref=text(body, "fallback", required=False),
                actor_public_id=auth.actor_public_id,
                decision=decision,
            )
    else:
        _authorize(auth, "read:referral", organization_public_id=org)
    with tenant_transaction():
        box = referral.inbox_dict(org)
        labels = trust.actor_labels()
        doctors = trust.clinicians_at(org)

    def who(ref: str) -> dict[str, str] | None:
        return {"id": ref, "name": labels.get(ref, "")} if ref else None

    return JsonResponse(
        {
            "owner": who(box["owner_ref"]) if box else None,
            "fallback": who(box["fallback_ref"]) if box else None,
            "doctors": [{"id": d, "name": labels.get(d, "")} for d in doctors],
            "can_manage": "R13" in auth.roles,
        }
    )


@endpoint("GET")
def my_referrals(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The patient's (or guardian's) referrals: only for people they may read."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:referral", subject_ref=p)]
    pg = _page_req(request, auth, "referrals_mine")
    rows: list[dict[str, Any]] = []
    if persons:
        with tenant_transaction():
            rows = referral.list_referrals(person_refs=persons, page=pg)
    return JsonResponse(_with_cursor({"referrals": _referrals_out(rows, auth)}, pg))


# ---------------------------------------------------------------- AI permits (off by default)
def _ai_admin(auth: trust.Authenticated) -> None:
    """TEMPORARY RULE (DEV-094): only an organization administrator (R13) manages AI permits,
    in their own organization. The lead decides who really may (backlog B14)."""
    _staff_only(auth)
    if "R13" not in auth.roles:
        raise ZoikoError(ErrorCode.POLICY_DENIED)


_AI_DAYS = (1, 365)


@endpoint("GET", "POST")
def staff_ai_permits(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: this organization's AI permits. POST: propose one (nothing is switched on by this)."""
    _ai_admin(auth)
    if request.method == "POST":
        body = json_body(request)
        model, prompt = text(body, "model"), text(body, "prompt")
        tools = [t for t in text(body, "tools", False).replace(",", " ").split() if t]
        with tenant_transaction():
            made = ai.create_permit(
                feature_id=text(body, "feature_id"),
                purpose=text(body, "purpose"),
                lane=text(body, "lane"),
                level=text(body, "level"),
                model_digest=ai.digest(model),
                prompt_digest=ai.digest(prompt),
                allowed_tools=tools,
                created_by_ref=auth.actor_public_id,
                clinical_owner_ref=text(body, "clinical_owner", False),
            )
        return JsonResponse({"permit": made}, status=201)
    with tenant_transaction():
        rows = ai.list_permits()
    return JsonResponse({"me": auth.actor_public_id, "permits": rows})


@endpoint("POST")
def staff_ai_permit_sign(
    request: HttpRequest, auth: trust.Authenticated, permit_id: str
) -> HttpResponse:
    _ai_admin(auth)
    with tenant_transaction():
        done = ai.sign_off(permit_id, actor_ref=auth.actor_public_id)
    return JsonResponse({"permit": done})


@endpoint("POST")
def staff_ai_permit_advance(
    request: HttpRequest, auth: trust.Authenticated, permit_id: str
) -> HttpResponse:
    """Move a permit to its next state. Authorizing takes `days` (how long it stays valid)."""
    _ai_admin(auth)
    body = json_body(request)
    expiry = None
    if text(body, "to") == "AUTHORIZED":
        try:
            days = int(body.get("days", 0))
        except (TypeError, ValueError):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["days"]) from None
        if not _AI_DAYS[0] <= days <= _AI_DAYS[1]:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["days"])
        expiry = utc_now() + dt.timedelta(days=days)
    with tenant_transaction():
        done = ai.advance(
            permit_id,
            text(body, "to"),
            actor_ref=auth.actor_public_id,
            reason=text(body, "reason", False),
            expiry=expiry,
        )
    return JsonResponse({"permit": done})


# --------------------------------------------------------------------------- retention (DEV-213)
def _retention_org(auth: trust.Authenticated, write: bool) -> str:
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:analytics", organization_public_id=org)
    if write:
        # Only the clinic's administrators set retention, and a second one must approve it.
        if "R13" not in auth.roles:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        _authorize(auth, "write:staff", organization_public_id=org)
    return org


@endpoint("GET", "POST")
def staff_retention(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """How long this clinic keeps each kind of data, and how many records are past it. Counts only:
    the product deletes nothing. POST proposes a rule (period_days null means no limit)."""
    from modules.retention import api as retention

    org = _retention_org(auth, write=request.method == "POST")
    status = 200
    if request.method == "POST":
        body = json_body(request)
        with tenant_transaction():
            retention.propose(
                organization_ref=org,
                data_class=text(body, "data_class"),
                period_days=body.get("period_days"),
                basis=body.get("basis"),
                by=auth.actor_public_id,
            )
        status = 201
    with tenant_transaction():
        found = retention.report(org)
    found["can_set"] = "R13" in auth.roles
    return JsonResponse(found, status=status)


@endpoint("POST")
def staff_retention_step(
    request: HttpRequest, auth: trust.Authenticated, rule_id: str, step: str
) -> HttpResponse:
    """A second administrator approves a proposed rule, or anyone of them withdraws it."""
    from modules.retention import api as retention

    actions = {"approve": retention.approve, "withdraw": retention.withdraw}
    if step not in actions:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    org = _retention_org(auth, write=True)
    with tenant_transaction():
        rule = actions[step](organization_ref=org, rule_ref=rule_id, by=auth.actor_public_id)
    return JsonResponse({"rule": rule})


# ------------------------------------------------------------------ partner booking system
# A clinic may choose a partner connector as its booking system (modules/bookingflow). Then a
# booking stays "requested" until that system itself confirms it. These endpoints choose the
# system, ask it, show where a booking stands, and let staff look after the connector operations.
CONNECTOR_ATTENTION = ("unknown", "dead")
CONNECTOR_WAITING = ("pending", "sent", "accepted")
CONNECTOR_RECENT = 20


def _connector_org(auth: trust.Authenticated) -> str:
    """The organization this staff-console (or platform) account acts in."""
    org = _staff_only(auth)
    if not org:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return org


@endpoint("GET", "POST")
def staff_booking_system(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Which partner connector confirms this clinic's bookings ("" = the clinic confirms by hand).

    Anyone on the clinic's staff may read it; only the administrator (R13) changes it."""
    from modules.bookingflow import api as bookingflow

    org = _connector_org(auth)
    _authorize(auth, "read:booking", organization_public_id=org)
    if request.method == "POST":
        if "R13" not in auth.roles:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        _authorize(auth, "write:staff", organization_public_id=org)
        system = json_body(request).get("system", "")
        if not isinstance(system, str):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["system"])
        allowed = _enabled_capabilities(auth)  # read in the platform's space, first
        with tenant_transaction():
            bookingflow.choose_system(org, system, by=auth.actor_public_id, capabilities=allowed)
    with tenant_transaction():
        chosen = bookingflow.system_of(org)
        decides = bookingflow.partner_decides(org)
    return JsonResponse(
        {"system": chosen, "options": bookingflow.options(), "can_set": "R13" in auth.roles,
         # False when no system, or it is down or no longer allowed: staff confirm by hand.
         "partner_decides": decides}
    )


@endpoint("POST")
def booking_ask_provider(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str
) -> HttpResponse:
    """Clinic staff ask the clinic's booking system to confirm a requested booking.

    The booking stays "requested" until the system itself answers; asking again while a question
    is still open asks nothing new."""
    from modules.bookingflow import api as bookingflow

    with tenant_transaction():
        booking = scheduling.get_booking(booking_id)
    org = booking["slot"]["organization_ref"]
    decision = _authorize(
        auth, "write:booking", organization_public_id=org, subject_ref=booking["person_ref"]
    )
    with tenant_transaction():
        stage = bookingflow.ask_provider(
            booking_ref=booking_id, actor_public_id=auth.actor_public_id, decision=decision
        )
        out = _booking_json(scheduling.get_booking(booking_id), {}, {}, trust.organization_names())
    out["provider_stage"] = stage
    return JsonResponse({"booking": out})


@endpoint("GET")
def booking_provider_stage(
    request: HttpRequest, auth: trust.Authenticated, booking_id: str
) -> HttpResponse:
    """Where a booking stands with its clinic's booking system (null if it was never asked).

    The clinic's staff see the state of the latest question too; the patient sees only the
    stage."""
    from modules.bookingflow import api as bookingflow

    with tenant_transaction():
        booking = scheduling.get_booking(booking_id)
    org = booking["slot"]["organization_ref"]
    _authorize(auth, "read:booking", organization_public_id=org, subject_ref=booking["person_ref"])
    staff = org in held_organizations(auth) and is_allowed(
        auth, "read:booking", organization_public_id=org
    )
    with tenant_transaction():
        stage = bookingflow.stages([booking_id]).get(booking_id)
    if stage and not staff:
        stage = {"stage": stage["stage"], "operation": ""}
    return JsonResponse({"booking": booking_id, "provider_stage": stage})


@endpoint("GET")
def staff_connector(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """This clinic's partner operations: those that need a person, those in flight, recent ones.

    Needing a person means the outcome is unknown or the connector gave up. References, states
    and codes only."""
    from modules.connector import api as connector

    org = _connector_org(auth)
    _authorize(auth, "read:workflow", organization_public_id=org)
    can_act = is_allowed(auth, "write:workflow", organization_public_id=org)
    with tenant_transaction():
        operations = connector.list_operations(organization_ref=org, limit=200)
    return JsonResponse(
        {
            "can_act": can_act,
            "attention": [o for o in operations if o["state"] in CONNECTOR_ATTENTION],
            "waiting": [o for o in operations if o["state"] in CONNECTOR_WAITING],
            "recent": [o for o in operations if o["state"] in ("confirmed", "rejected")][
                :CONNECTOR_RECENT
            ],
        }
    )


@endpoint("GET")
def staff_connector_history(
    request: HttpRequest, auth: trust.Authenticated, operation_id: str
) -> HttpResponse:
    """The steps of one partner operation of this clinic, oldest first, and the chain check."""
    from modules.connector import api as connector

    org = _connector_org(auth)
    _authorize(auth, "read:workflow", organization_public_id=org)
    with tenant_transaction():
        history = connector.operation_history(operation_id, org)
        labels = trust.actor_labels()
    for event in history["events"]:
        ref = event.pop("actor_ref")
        event["by"] = "System" if ref == "system" else labels.get(ref, "Someone")
    return JsonResponse({"history": history})


@endpoint("POST")
def staff_connector_redrive(
    request: HttpRequest, auth: trust.Authenticated, operation_id: str
) -> HttpResponse:
    """Put a partner operation that gave up back in the queue (its attempts start again)."""
    from modules.connector import api as connector

    org = _connector_org(auth)
    _authorize(auth, "write:workflow", organization_public_id=org)
    with tenant_transaction():
        done = connector.redrive(
            operation_ref=operation_id, actor_ref=auth.actor_public_id, organization_ref=org
        )
    return JsonResponse({"operation": done})


@endpoint("POST")
def staff_connector_resolve(
    request: HttpRequest, auth: trust.Authenticated, operation_id: str
) -> HttpResponse:
    """Record, after checking with the partner, how an unknown or given-up operation ended.

    The record keeps who settled it, and it never counts as the partner's own confirmation: a
    booking it was about stays "requested"."""
    from modules.connector import api as connector

    org = _connector_org(auth)
    _authorize(auth, "write:workflow", organization_public_id=org)
    outcome = text(json_body(request), "outcome")
    with tenant_transaction():
        done = connector.resolve_by_hand(
            operation_ref=operation_id,
            outcome=outcome,
            actor_ref=auth.actor_public_id,
            organization_ref=org,
        )
    return JsonResponse({"operation": done})


# ------------------------------------------------------------------ clinical references (DEV-041)
def _clinical_references_on() -> None:
    """Clinical references are switched off unless CLINICAL_REFERENCES_ENABLED is True. While off,
    every endpoint below answers "not found", the same for everyone, before anything else."""
    from modules.clinicalref import api as clinicalref

    if not clinicalref.enabled():
        raise ZoikoError(ErrorCode.NOT_FOUND)


def _reference_json(row: dict[str, Any], orgs: dict[str, str]) -> dict[str, Any]:
    """A reference with the clinic's name added, so the patient knows whom to ask."""
    return {
        **row,
        "organization": {"id": row["organization"], "name": orgs.get(row["organization"], "")},
    }


@endpoint("GET", "POST")
def clinician_patient_references(
    request: HttpRequest, auth: trust.Authenticated, person_id: str
) -> HttpResponse:
    """Pointers to a patient's records kept in a clinic's OWN clinical system: never the record.

    GET: every reference for the patient, withdrawn ones included, with the record's id in that
    system (a doctor with a care relationship; reading for continuity of care is enough).
    POST `{"system", "external_id", "kind", "refers_to"}`: a doctor treating the patient, for
    direct care, records that such a record exists at their clinic. No text, no values, no file."""
    from modules.clinicalref import api as clinicalref

    _clinical_references_on()
    _clinician_only(auth)
    if request.method == "GET":
        _authorize(auth, "read:diagnostic", subject_ref=person_id)
        with tenant_transaction():
            rows = clinicalref.list_for_person(
                person_id, viewer_ref=auth.actor_public_id, care_team=True
            )
            orgs = trust.organization_names()
        return JsonResponse({"references": [_reference_json(r, orgs) for r in rows]})
    org = _catalog_org(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    body = json_body(request)
    decision = _authorize(
        auth, clinicalref.OPERATION, organization_public_id=org, subject_ref=person_id
    )
    with tenant_transaction():
        made = clinicalref.add(
            person_ref=person_id,
            organization_ref=org,
            system=body.get("system"),
            external_id=body.get("external_id"),
            kind=body.get("kind"),
            refers_to=body.get("refers_to"),
            by=auth.actor_public_id,
            decision=decision,
        )
        orgs = trust.organization_names()
    return JsonResponse({"reference": _reference_json(made, orgs)}, status=201)


@endpoint("POST")
def clinician_reference_withdraw(
    request: HttpRequest, auth: trust.Authenticated, reference_id: str
) -> HttpResponse:
    """Withdraw a reference `{"reason": "entered_in_error" | "wrong_patient" |
    "removed_at_source" | "other"}`. A doctor of the clinic that added it, treating the patient,
    for direct care. The reference stays, marked withdrawn; nothing is deleted."""
    from modules.clinicalref import api as clinicalref

    _clinical_references_on()
    _clinician_only(auth)
    with tenant_transaction():
        found = clinicalref.get(reference_id)
    if found["organization"] not in held_organizations(auth):
        raise ZoikoError(ErrorCode.NOT_FOUND)  # another clinic's pointer: not theirs to see
    reason = json_body(request).get("reason")
    decision = _authorize(
        auth,
        clinicalref.OPERATION,
        organization_public_id=found["organization"],
        subject_ref=found["person"],
    )
    with tenant_transaction():
        done = clinicalref.withdraw(
            reference_ref=reference_id, reason=reason, by=auth.actor_public_id, decision=decision
        )
        orgs = trust.organization_names()
    return JsonResponse({"reference": _reference_json(done, orgs)})


# ------------------------------------------------------------------ consultation notes (DEV-304)
def _notes_on() -> Any:
    from modules.clinicalnote import api as clinicalnote

    if not clinicalnote.enabled():
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return clinicalnote


def _note_json(note: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    out = dict(note)
    out["author_name"] = names.get(str(note.get("author", "")), "")
    out["mine"] = False
    return out


@endpoint("GET", "POST")
def clinician_notes(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET ?patient=: this patient's notes (no text). POST {patient, note_date, body}: start a
    draft. A doctor treating the patient only, for direct care."""
    notes = _notes_on()
    _clinician_only(auth)
    org = _catalog_org(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    if request.method == "POST":
        body = json_body(request)
        person = text(body, "patient")
        decision = _authorize(
            auth, notes.WRITE, organization_public_id=org, subject_ref=person
        )
        with tenant_transaction():
            made = notes.create_draft(
                person_ref=person,
                organization_ref=org,
                body=body.get("body"),
                note_date=body.get("note_date"),
                by=auth.actor_public_id,
                decision=decision,
                diagnoses=body.get("diagnoses"),
                medications=body.get("medications"),
            )
            names = trust.actor_labels()
        out = _note_json(made, names)
        out["mine"] = True
        return JsonResponse({"note": out}, status=201)
    person = request.GET.get("patient") or ""
    if not person:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["patient"])
    decision = _authorize(auth, notes.READ, organization_public_id=org, subject_ref=person)
    paged = "limit" in request.GET or "cursor" in request.GET
    next_cursor: str | None = None
    with tenant_transaction():
        if paged:
            rows, next_cursor = notes.page_for_person(
                person_ref=person,
                by=auth.actor_public_id,
                decision=decision,
                limit=pagination.limit_from(request.GET.get("limit")),
                cursor=request.GET.get("cursor") or None,
                organization_ref=org,
            )
        else:
            rows = notes.list_for_person(
                person_ref=person,
                by=auth.actor_public_id,
                decision=decision,
                organization_ref=org,
            )
        names = trust.actor_labels()
    checking = identity_held([person])  # DEV-385
    shown = []
    for row in rows:
        item = _note_json(row, names)
        item["mine"] = row["author"] == auth.actor_public_id
        item["identity_check_open"] = person in checking
        shown.append(item)
    return JsonResponse({"notes": shown, **({"next_cursor": next_cursor} if paged else {})})


def _note_found(notes: Any, auth: trust.Authenticated, note_id: str) -> dict[str, Any]:
    with tenant_transaction():
        found = notes.get(note_id)
    if found["organization"] not in held_organizations(auth):
        raise ZoikoError(ErrorCode.NOT_FOUND)  # another clinic's note: not theirs to see
    return found


@endpoint("GET")
def clinician_note(request: HttpRequest, auth: trust.Authenticated, note_id: str) -> HttpResponse:
    """One note with its text. Every read is recorded."""
    notes = _notes_on()
    _clinician_only(auth)
    found = _note_found(notes, auth, note_id)
    if found["state"] == "draft" and found["author"] != auth.actor_public_id:
        raise ZoikoError(ErrorCode.NOT_FOUND)  # a draft is its author's alone
    decision = _authorize(
        auth, notes.READ, organization_public_id=found["organization"],
        subject_ref=found["person"],
    )
    with tenant_transaction():
        got = notes.read(note_ref=note_id, by=auth.actor_public_id, decision=decision)
        names = trust.actor_labels()
    out = _note_json(got, names)
    out["mine"] = got["author"] == auth.actor_public_id
    out["identity_check_open"] = bool(identity_held([got["person"]]))
    return JsonResponse({"note": out})


@endpoint("POST")
def clinician_note_step(
    request: HttpRequest, auth: trust.Authenticated, note_id: str, step: str
) -> HttpResponse:
    """edit {body} (a draft you wrote) | sign | amend {reason} | addendum {body} |
    guardian {hidden}. A signed note is never edited: amend starts a new version that replaces it
    once signed."""
    notes = _notes_on()
    _clinician_only(auth)
    if step not in ("edit", "sign", "amend", "addendum", "guardian"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    found = _note_found(notes, auth, note_id)
    body = json_body(request)
    op = notes.ATTEST if step == "sign" else notes.WRITE
    decision = _authorize(
        auth, op, organization_public_id=found["organization"], subject_ref=found["person"]
    )
    if step == "sign":
        refuse_if_identity_held(found["person"])  # DEV-385: held during duplicate check
    with tenant_transaction():
        if step == "edit":
            done = notes.edit_draft(
                note_ref=note_id, body=body.get("body"), by=auth.actor_public_id,
                decision=decision, diagnoses=body.get("diagnoses"),
                medications=body.get("medications"),
            )
        elif step == "addendum":
            done = notes.add_addendum(
                note_ref=note_id, body=body.get("body"), by=auth.actor_public_id,
                decision=decision,
            )
        elif step == "guardian":  # {hidden: bool}: keep this note from a guardian (DEV-324)
            done = notes.set_guardian_hidden(
                note_ref=note_id, hidden=body.get("hidden"), by=auth.actor_public_id,
                decision=decision,
            )
        elif step == "sign":
            done = notes.sign(note_ref=note_id, by=auth.actor_public_id, decision=decision)
        else:
            done = notes.amend(
                note_ref=note_id, reason=body.get("reason"), by=auth.actor_public_id,
                decision=decision,
            )
        names = trust.actor_labels()
    out = _note_json(done, names)
    out["mine"] = True
    return JsonResponse({"note": out}, status=201 if step == "amend" else 200)


@endpoint("POST")
def clinician_note_attachments(
    request: HttpRequest, auth: trust.Authenticated, note_id: str
) -> HttpResponse:
    """Attach a document or image (PDF, PNG, JPEG) to a draft note (DEV-376). At most 10 MB."""
    notes = _notes_on()
    _clinician_only(auth)
    found = _note_found(notes, auth, note_id)
    decision = _authorize(
        auth, notes.WRITE, organization_public_id=found["organization"], subject_ref=found["person"]
    )
    data = request.read(10 * 1024 * 1024 + 1)
    with tenant_transaction():
        done = notes.add_attachment(
            note_ref=note_id,
            filename=request.GET.get("filename") or "",
            data=data,
            by=auth.actor_public_id,
            decision=decision,
        )
    return JsonResponse({"attachment": done}, status=201)


@endpoint("GET", "DELETE")
def clinician_note_attachment_item(
    request: HttpRequest, auth: trust.Authenticated, note_id: str, attachment_id: str
) -> HttpResponse:
    """Download an attachment (clean scan only) or delete it from a draft note."""
    notes = _notes_on()
    _clinician_only(auth)
    found = _note_found(notes, auth, note_id)
    if request.method == "DELETE":
        decision = _authorize(
            auth, notes.WRITE, organization_public_id=found["organization"],
            subject_ref=found["person"],
        )
        with tenant_transaction():
            notes.remove_attachment(
                note_ref=note_id, attachment_ref=attachment_id, by=auth.actor_public_id,
                decision=decision,
            )
        return JsonResponse({"deleted": True})
    decision = _authorize(
        auth, notes.READ, organization_public_id=found["organization"], subject_ref=found["person"]
    )
    with tenant_transaction():
        name, ctype, data = notes.open_attachment(
            note_ref=note_id, attachment_ref=attachment_id, by=auth.actor_public_id,
            decision=decision,
        )
    response = HttpResponse(data, content_type=ctype)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


def _note_people(auth: trust.Authenticated, notes: Any) -> dict[str, bool]:
    """Whose notes this login may read in the patient app: {person: as_guardian}. Their own; a
    child's only while the child is under 16 (DEV-324); an adult's they act for as attorney or
    court deputy (DEV-353, LEAD DECISION). The policy engine has already required the "notes"
    part of the grant, so without it there is nothing. A note its author kept from guardians is
    kept from attorneys and deputies too. Access by guardians and adult proxies ends when the
    patient dies (AHRA 1990, DEV-377)."""
    people = _my_people(auth, notes.READ)
    with tenant_transaction():
        own = trust.own_person_of(auth.actor_public_id)
        others = [p for p in people if p != own]
        births = trust.get_person_profiles(others) if others else {}
        adults = trust.adult_proxy_persons(auth.actor_public_id) if others else set()
        deceased = set(trust.death_dates(people).keys()) if people else set()
    out = {p: False for p in people if p == own and p not in deceased}
    for person in others:
        if person in deceased:
            continue
        if person in adults or notes.guardian_may_read(births.get(person, {}).get("birth_date")):
            out[person] = True
    return out


@endpoint("GET")
def my_notes(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The patient's own signed consultation notes, once the delay has passed (no text here).
    Drafts are never shown. A guardian sees a child's notes only with the "notes" part of their
    grant and only while the child is under 16, never a note kept from guardians (DEV-324).
    With the notes switch off the answer is an empty list marked `enabled: false`, so the app can
    say so without an error."""
    from modules.clinicalnote import api as clinicalnote

    if not clinicalnote.enabled():
        return JsonResponse({"notes": [], "enabled": False})
    notes = _notes_on()
    people = _note_people(auth, notes)
    checking = identity_held(list(people.keys()))  # DEV-385
    rows: list[dict[str, Any]] = []
    for person, as_guardian in people.items():
        decision = _authorize(auth, notes.READ, subject_ref=person)
        with tenant_transaction():
            found = notes.patient_list(
                person_ref=person, by=auth.actor_public_id, decision=decision,
                as_guardian=as_guardian,
            )
            who = trust.profile_summary_for([person]).get(person, "") if as_guardian else ""
        for row in found:
            row["for_name"] = who or None
            if person in checking:
                row["held"] = IDENTITY_CHECK
        rows += found
    with tenant_transaction():
        names = trust.actor_labels()
    for row in rows:
        row["author_name"] = names.get(str(row["author"]), "")
        row.pop("author", None)
    rows.sort(key=lambda r: (r["note_date"], r["version"]), reverse=True)
    return JsonResponse({"notes": rows})


@endpoint("GET")
def my_note(request: HttpRequest, auth: trust.Authenticated, note_id: str) -> HttpResponse:
    """One of the patient's own notes, with its text. Recorded."""
    notes = _notes_on()
    with tenant_transaction():
        found = notes.get(note_id)
    people = _note_people(auth, notes)
    if found["person"] not in people:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    decision = _authorize(auth, notes.READ, subject_ref=found["person"])
    if identity_held([found["person"]]):  # held during a duplicate-record check (DEV-385)
        raise ZoikoError(ErrorCode.NOT_FOUND)
    with tenant_transaction():
        got = notes.patient_read(
            note_ref=note_id, person_ref=found["person"], by=auth.actor_public_id,
            decision=decision, as_guardian=people[found["person"]],
        )
        names = trust.actor_labels()
    got["author_name"] = names.get(str(got["author"]), "")
    got.pop("author", None)
    return JsonResponse({"note": got})


@endpoint("GET")
def my_note_attachment(
    request: HttpRequest, auth: trust.Authenticated, note_id: str, attachment_id: str
) -> HttpResponse:
    """Download an attachment on a note for the patient (or guardian) after the 24h delay
    (DEV-376)."""
    notes = _notes_on()
    people = _note_people(auth, notes)
    with tenant_transaction():
        found = notes.get(note_id)
    if found["person"] not in people:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    decision = _authorize(auth, notes.READ, subject_ref=found["person"])
    if identity_held([found["person"]]):  # held during a duplicate-record check (DEV-385)
        raise ZoikoError(ErrorCode.NOT_FOUND)
    with tenant_transaction():
        name, ctype, data = notes.patient_open_attachment(
            note_ref=note_id,
            attachment_ref=attachment_id,
            person_ref=found["person"],
            by=auth.actor_public_id,
            decision=decision,
            as_guardian=people[found["person"]],
        )
    response = HttpResponse(data, content_type=ctype)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@endpoint("GET")
def my_references(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The patient's own references: what kind of record, its date, which clinic and system holds
    it, and whether it was withdrawn, so they know whom to ask. Never the record's id in that
    system. A guardian needs the "records" part of their grant, which is not granted yet."""
    from modules.clinicalref import api as clinicalref

    _clinical_references_on()
    people = _my_people(auth, "read:diagnostic")
    with tenant_transaction():
        rows = [
            row
            for person in people
            for row in clinicalref.list_for_person(
                person, viewer_ref=auth.actor_public_id, care_team=False
            )
        ]
        orgs = trust.organization_names()
    return JsonResponse({"references": [_reference_json(r, orgs) for r in rows]})


# ------------------------------------------------------------------ paying a bill online (skeleton)
def _payer(auth: trust.Authenticated) -> str:
    """The patient this login IS, after the policy engine allows them their own billing. Paying is
    for the person who owes the bill: a guardian or staff login gets 403."""
    org = _catalog_org(auth)
    with tenant_transaction():
        person = trust.own_person_of(auth.actor_public_id)
    if not person or org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:billing", organization_public_id=org, subject_ref=person)
    return person


@endpoint("GET", "POST")
def my_invoice_payments(
    request: HttpRequest, auth: trust.Authenticated, invoice_id: str
) -> HttpResponse:
    """GET: the attempts to pay one of my invoices. POST {idempotency_key} (or an Idempotency-Key
    header): pay its balance through the deployment's payment provider. Refused with 422
    (field payment_provider) while payments are switched off, which is the default."""
    person = _payer(auth)
    if request.method == "POST":
        body = json_body(request)
        key = text(body, "idempotency_key", required=False) or request.headers.get(
            "Idempotency-Key", ""
        )
        made = billing.start_payment(
            invoice_ref=invoice_id,
            person_ref=person,
            idempotency_key=key,
            actor_public_id=auth.actor_public_id,
        )  # runs its own transactions
        return JsonResponse({"payment": made}, status=200 if made["replayed"] else 201)
    with tenant_transaction():
        rows = billing.payer_payments(invoice_id, person)
    return JsonResponse({"payments": rows})


@endpoint("POST")
def my_payment_refresh(
    request: HttpRequest, auth: trust.Authenticated, payment_id: str
) -> HttpResponse:
    """Ask the payment provider again about one of my payments that is pending or unknown."""
    person = _payer(auth)
    done = billing.refresh_payment(
        payment_ref=payment_id, person_ref=person, actor_public_id=auth.actor_public_id
    )
    return JsonResponse({"payment": done})


@endpoint("GET")
def staff_billing_payments(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Attempts to pay this clinic's invoices through a provider (?invoice= for one invoice)."""
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    _authorize(auth, "read:billing", organization_public_id=org)
    pg = _page_req(request, auth, f"payments:{request.GET.get('invoice') or ''}")
    with tenant_transaction():
        rows = billing.list_payments(org, request.GET.get("invoice") or None, page=pg)
    return JsonResponse(_with_cursor({"payments": rows}, pg))


# --------------------------------------------------------------------------- circuits (DEV-221)
@endpoint("GET")
def staff_connector_circuits(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Which of this customer's partner connectors are paused or halted, and since when."""
    from modules.connector import api as connector

    org = _connector_org(auth)
    _authorize(auth, "read:workflow", organization_public_id=org)
    with tenant_transaction():
        found = connector.circuits()
    return JsonResponse(
        {
            "circuits": found,
            "can_act": is_allowed(auth, "write:workflow", organization_public_id=org),
        }
    )


@endpoint("POST")
def staff_connector_circuit_reset(
    request: HttpRequest, auth: trust.Authenticated, connector_key: str
) -> HttpResponse:
    """A person starts a paused or halted connector again (after the partner fixed it)."""
    from modules.connector import api as connector

    org = _connector_org(auth)
    _authorize(auth, "write:workflow", organization_public_id=org)
    with tenant_transaction():
        done = connector.reset_circuit(connector_key, actor_ref=auth.actor_public_id)
    return JsonResponse({"circuit": done})


# ------------------------------------------------------------------ certification (DEV-230)
@endpoint("GET")
def platform_connectors(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Every partner system this deployment has, and how far its current version is approved."""
    from modules.connector import api as connector

    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        found = connector.certification.overview()
        labels = trust.actor_labels()
    for item in found:
        cert = item["certification"]
        if cert:
            for field in ("enrolled_by", "certified_by", "live_by"):
                cert[field] = labels.get(cert[field], "Zoiko staff") if cert[field] else ""
    return JsonResponse({"connectors": found, "can_write": "R11" in auth.roles,
                         "can_attest": "R12" in auth.roles})


@endpoint("POST")
def platform_connector_step(
    request: HttpRequest, auth: trust.Authenticated, connector_key: str, step: str
) -> HttpResponse:
    """enrol | certify | live | suspend, each by the person the rules allow."""
    from modules.connector import api as connector

    org = _platform_only(auth)
    steps = {"enrol": connector.certification.enrol, "certify": connector.certification.certify,
             "live": connector.certification.go_live}
    if step not in (*steps, "suspend"):
        raise ZoikoError(ErrorCode.NOT_FOUND)
    safety = "R12" in auth.roles and "R11" not in auth.roles
    attesting = step == "certify" or (step == "suspend" and safety)
    decision = _authorize(
        auth, "attest:platform" if attesting else "write:platform", organization_public_id=org)
    with tenant_transaction():
        if step == "suspend":
            done = connector.certification.suspend(
                key=connector_key, note=text(json_body(request), "note"),
                actor_public_id=auth.actor_public_id, decision=decision, organization_ref=org)
        else:
            done = steps[step](key=connector_key, actor_public_id=auth.actor_public_id,
                               decision=decision, organization_ref=org)
    return JsonResponse({"certification": done}, status=201 if step == "enrol" else 200)
