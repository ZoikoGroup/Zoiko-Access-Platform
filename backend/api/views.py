"""The /api/v1 endpoints used by the web apps.

Each one: logged-in actor -> tenant bound -> policy decision (recorded, even when denied) ->
domain function -> JSON. Errors come out as problem+json from the middleware.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.idempotency import run as run_once
from foundation.mail import mail_stats
from foundation.outbox import backlog_stats
from foundation.timeutil import get_zone, utc_now
from modules.ai import api as ai
from modules.bookingflow import api as bookingflow
from modules.catalog import api as catalog
from modules.comms import api as comms
from modules.coverage import api as coverage
from modules.evidence import api as evidence
from modules.messaging import api as messaging
from modules.policy import api as policy
from modules.referral import api as referral
from modules.safety import api as safety
from modules.scheduling import api as scheduling
from modules.trust import api as trust
from modules.waitlist import api as waitlist
from modules.workflow import api as workflow

from .support import (
    endpoint,
    held_organizations,
    home_for,
    is_allowed,
    json_body,
    purpose_for,
    require_feature,
    text,
)

IDEMPOTENCY_RETENTION = dt.timedelta(days=1)


def _authorize(auth: trust.Authenticated, operation: str, **kwargs: Any) -> policy.Decision:
    return policy.authorize(
        actor_public_id=auth.actor_public_id, operation=operation,
        purpose_code=purpose_for(auth), **kwargs,
    )


def _catalog_org(auth: trust.Authenticated) -> str | None:
    orgs = held_organizations(auth)
    return orgs[0] if orgs else None


def _names(auth: trust.Authenticated, refs: set[str]) -> dict[str, str]:
    """Display names for the patients THIS login may read (the policy engine decides for each).
    Anyone else's name is simply absent, and the apps then show the patient's id."""
    org = _catalog_org(auth)
    allowed = [p for p in sorted(refs) if p and is_allowed(
        auth, "read:person", organization_public_id=org, subject_ref=p)]
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
    booking: dict[str, Any], services: dict[str, Any], locations: dict[str, Any],
    orgs: dict[str, str], labels: dict[str, str] | None = None,
    verified: set[str] | None = None,
) -> dict[str, Any]:
    slot = booking["slot"]
    service = services.setdefault(slot["service_ref"], catalog.get_service(slot["service_ref"]))
    location = locations.setdefault(
        slot["location_ref"], catalog.get_location(slot["location_ref"]))
    return {
        "id": booking["public_id"], "status": booking["status"], "person": booking["person_ref"],
        "created_at": booking["created_at"],
        "service": {"id": service["public_id"], "name": service["name"],
                    "mode": service["mode"]},
        "organization": {"id": slot["organization_ref"],
                         "name": orgs.get(slot["organization_ref"], "")},
        "location": {"id": location["public_id"], "name": location["name"],
                     "address": location["address"], "phone": location["phone"]},
        "starts_at": slot["starts_at"], "ends_at": slot["ends_at"],
        "local_start": slot["local_start"], "local_zone": slot["local_zone"],
        "provider": _provider_json(slot, labels, verified),
        "rescheduled_from": booking.get("rescheduled_from_ref") or None,
        "attendance": booking.get("attendance") or None,
        "cancel_reason": booking.get("cancel_reason") or None,
        "pending_change": booking.get("pending_change") or None,
        "late": _waiting_too_long(booking),
        "provider_stage": booking.get("provider_stage"),
    }


def _waiting_too_long(booking: dict[str, Any]) -> bool:
    """A booking still waiting for confirmation after the clinic's response target."""
    if booking["status"] != "requested":
        return False
    waited = utc_now() - dt.datetime.fromisoformat(booking["created_at"])
    target = trust.response_target_hours(booking["slot"]["organization_ref"])
    return waited > dt.timedelta(hours=target)


@endpoint("GET")
def me(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    with tenant_transaction():
        orgs = trust.organization_names()
        roles = trust.active_roles(auth.actor_public_id)
        persons = trust.reachable_persons(auth.actor_public_id)
        email = trust.email_status(auth.actor_public_id)
        display_name = trust.display_name_of(auth.actor_public_id)
        check = trust.name_check_states(only=auth.actor_public_id).get(
            auth.actor_public_id, {"state": "none"})
    return JsonResponse({
        "actor": auth.actor_public_id, "type": auth.actor_type, "tenant": auth.tenant_name,
        "cell": auth.context.cell_id, "purpose": purpose_for(auth), "persons": persons,
        "home": home_for(auth.actor_type, [r for r, _ in roles], auth.tenant_type),
        "email": email, "display_name": display_name,
        # Only the state and what was checked: the administrator's note stays with the clinic.
        "name_check": {k: check[k] for k in (
            "state", "basis_text", "checked_on", "reason") if k in check},
        "roles": [{"role": r, "organization": {"id": o, "name": orgs.get(o, "")}}
                  for r, o in roles],
    })


def _service_json(s: dict[str, Any], orgs: dict[str, str]) -> dict[str, Any]:
    upcoming = scheduling.list_open_slots(s["public_id"], limit=1)
    return {
        "id": s["public_id"], "name": s["name"], "category": s["category"],
        "description": s["description"], "duration_minutes": s["duration_minutes"],
        "mode": s["mode"], "status": s["status"], "listing_state": s["listing_state"],
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
            rows = [s for s in catalog.list_services(active_only=False)
                    if s["organization_ref"] == org]
            waiting = waitlist.waiting_counts(org or "")
            labels = trust.actor_labels()
            return JsonResponse({"services": [
                {**_service_json(s, orgs), "waiting": waiting.get(s["public_id"], 0),
                 "listing": {
                     "evidence_note": s["evidence_note"],
                     "requested_by": labels.get(s["requested_by_ref"], ""),
                     "reviewed_by": labels.get(s["reviewed_by_ref"], ""),
                     "you_requested": s["requested_by_ref"] == auth.actor_public_id,
                     "can_approve": "R13" in auth.roles}}
                for s in rows]})
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
            query=request.GET.get("q", "")[:100], category=request.GET.get("category", "")[:64],
            mode=mode, organization_ref=request.GET.get("organization", ""))
        usual = scheduling.usual_confirmation_hours()
        out = [{**_service_json(s, orgs),
                "usual_confirmation_hours": usual.get(s["organization_ref"])} for s in rows]
        if request.GET.get("available"):
            out = [s for s in out if s["next_slot"]]          # only services with a free time
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
            organization_ref=org, name=text(body, "name"), category=text(body, "category"),
            duration_minutes=duration, mode=text(body, "mode", False) or "in_person",
            description=text(body, "description", False),
            actor_public_id=auth.actor_public_id, decision=decision)
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
                organization_ref=org, name=text(body, "name"), time_zone=text(body, "time_zone"),
                address=text(body, "address", False), phone=text(body, "phone", False),
                actor_public_id=auth.actor_public_id, decision=decision)
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
            location_ref=location_id, phone=text(body, "phone", False),
            address=text(body, "address", False), actor_public_id=auth.actor_public_id,
            decision=decision)
    return JsonResponse({"location": _location_json(row)})


def _location_json(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["public_id"], "name": row["name"], "address": row["address"],
            "phone": row["phone"], "time_zone": row["time_zone"],
            "organization": row["organization_ref"]}


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
            service_id, text(body, "action"), note=text(body, "note", False),
            actor_public_id=auth.actor_public_id, decision=decision)
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
            service_id, text(body, "status"),
            actor_public_id=auth.actor_public_id, decision=decision)
        out = _service_json(row, trust.organization_names())
    return JsonResponse({"service": out})


def _add_slots(
    request: HttpRequest, auth: trust.Authenticated, service_id: str
) -> HttpResponse:
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
            service_ref=service_id, location_ref=text(body, "location"), first_local=first,
            count=count, step_minutes=step, provider_ref=text(body, "provider", False),
            days=days, weekdays_only=weekdays_only,
            actor_public_id=auth.actor_public_id, decision=decision)
    return JsonResponse({"created": len(made)}, status=201)


@endpoint("POST")
def slot_withdraw(request: HttpRequest, auth: trust.Authenticated, slot_id: str) -> HttpResponse:
    """Take one free future time off the calendar. Refused if it has a booking."""
    with tenant_transaction():
        org = scheduling.get_slot(slot_id)["organization_ref"]
    decision = _authorize(auth, "write:catalog", organization_public_id=org)
    with tenant_transaction():
        scheduling.withdraw_slot(
            slot_ref=slot_id, actor_public_id=auth.actor_public_id, decision=decision)
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
            service_ref=service_id, first_day=first, last_day=last,
            provider_ref=text(body, "provider", False), dry_run=dry_run,
            actor_public_id=auth.actor_public_id, decision=decision)
    return JsonResponse({"count": count, "dry_run": dry_run})


@endpoint("GET", "POST")
def service_slots(
    request: HttpRequest, auth: trust.Authenticated, service_id: str
) -> HttpResponse:
    if request.method == "POST":
        return _add_slots(request, auth, service_id)
    _authorize(auth, "read:catalog", organization_public_id=_catalog_org(auth))
    with tenant_transaction():
        service = catalog.get_service(service_id)
        slots = scheduling.list_open_slots(service_id)
        names = {s["location_ref"]: catalog.get_location(s["location_ref"])["name"] for s in slots}
        people = trust.actor_labels()
        checked = trust.verified_providers()
    return JsonResponse({
        "service": {"id": service["public_id"], "name": service["name"],
                    "duration_minutes": service["duration_minutes"]},
        "slots": [{"id": s["public_id"], "starts_at": s["starts_at"], "ends_at": s["ends_at"],
                   "local_start": s["local_start"], "local_zone": s["local_zone"],
                   "location": names[s["location_ref"]],
                   "provider": _provider_json(s, people, checked)} for s in slots],
    })


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
        stages = bookingflow.stages([r["id"] for r in rows])
    mine = {o for o in held_organizations(auth)
            if is_allowed(auth, "read:booking", organization_public_id=o)}   # staff, not patients
    for row in rows:
        stage = stages.get(row["id"])
        if stage:                       # the clinic sees the detail; a patient only the stage
            row["provider_stage"] = stage if row["organization"]["id"] in mine else {
                "stage": stage["stage"], "operation": ""}
    rows.sort(key=lambda r: r["starts_at"])
    return JsonResponse({"bookings": _add_names(auth, rows)})


def _create_booking(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    require_feature("booking.request")
    body = json_body(request)
    slot_ref = text(body, "slot")
    key = request.headers.get("Idempotency-Key", "")
    with tenant_transaction():
        person = text(body, "person", required=False)
        if not person:
            mine = trust.reachable_persons(auth.actor_public_id)
            if len(mine) != 1:       # none, or several: the caller must say which
                raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["person"])
            person = mine[0]
        slot = scheduling.get_slot(slot_ref)
    decision = _authorize(auth, "write:booking", organization_public_id=slot["organization_ref"],
                          subject_ref=person)
    with tenant_transaction():
        outcome = run_once(
            scope=f"actor:{auth.actor_public_id} POST /bookings", key=key, payload=body,
            retain_for=IDEMPOTENCY_RETENTION,
            work=lambda: {"booking": scheduling.book(
                slot_ref=slot_ref, person_ref=person, actor_public_id=auth.actor_public_id,
                decision=decision)["public_id"]},
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
    decision = _authorize(auth, "write:booking", organization_public_id=org,
                          subject_ref=booking["person_ref"])
    with tenant_transaction():
        if verb == "confirm" and bookingflow.system_of(org):
            # This clinic's bookings are confirmed by its booking system: ask it. The booking
            # stays "requested" until the system itself answers (modules/bookingflow).
            stage = bookingflow.ask_provider(
                booking_ref=booking_id, actor_public_id=auth.actor_public_id, decision=decision)
            done = scheduling.get_booking(booking_id) | {"provider_stage": stage}
        elif verb == "confirm":
            done = scheduling.confirm(
                booking_ref=booking_id, actor_public_id=auth.actor_public_id, decision=decision)
        else:
            reason = text(json_body(request), "reason", required=False)
            done = scheduling.cancel(
                booking_ref=booking_id, actor_public_id=auth.actor_public_id, decision=decision,
                reason=reason)
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


def _ics_text(value: str) -> str:
    """Escape text for an iCalendar line (RFC 5545): backslash, semicolon, comma, line breaks."""
    return (value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", " ").replace("\n", " "))


_EVENT_WORDS = {   # what the step is called, the same for everyone
    "requested": "Requested", "confirmed": "Confirmed", "cancelled": "Cancelled",
    "moved_away": "Moved to another time", "moved_here": "Booked as the new time",
    "attended": "Attended", "no_show": "Did not attend", "reminder_sent": "Reminder sent",
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
            {"seq": e["seq"], "event": e["event_type"], "text": _EVENT_WORDS[e["event_type"]],
             "from": e["state_from"], "to": e["state_to"], "reason": e["reason_code"],
             "who": _who(e["source"], e["actor_ref"], patient_side, labels),
             "at": e["occurred_at"]}
            for e in data["events"]],
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
        raise ZoikoError(ErrorCode.NOT_FOUND)             # nothing to add to a calendar
    with tenant_transaction():
        row = _booking_json(booking, {}, {}, trust.organization_names())
    stamp = "%Y%m%dT%H%M%SZ"
    start = dt.datetime.fromisoformat(row["starts_at"]).astimezone(dt.timezone.utc)
    end = dt.datetime.fromisoformat(row["ends_at"]).astimezone(dt.timezone.utc)
    place = (row["location"]["name"] + " " + row["location"]["address"]).strip()
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Zoiko Access//Appointments//EN",
        "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:{row['id']}@zoikoaccess",
        f"DTSTAMP:{utc_now().strftime(stamp)}", f"DTSTART:{start.strftime(stamp)}",
        f"DTEND:{end.strftime(stamp)}",
        f"SUMMARY:{_ics_text(row['service']['name'] + ' at ' + row['organization']['name'])}",
        f"LOCATION:{_ics_text(place)}",
        "STATUS:" + ("CONFIRMED" if row["status"] == "confirmed" else "TENTATIVE"),
        "END:VEVENT", "END:VCALENDAR",
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
    decision = _authorize(auth, "write:booking", organization_public_id=org,
                          subject_ref=booking["person_ref"])
    with tenant_transaction():
        done = scheduling.reschedule(
            booking_ref=booking_id, new_slot_ref=new_slot, actor_public_id=auth.actor_public_id,
            decision=decision)
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
    decision = _authorize(auth, "write:booking",
                          organization_public_id=booking["slot"]["organization_ref"],
                          subject_ref=booking["person_ref"])
    with tenant_transaction():
        done = scheduling.mark_attendance(
            booking_ref=booking_id, outcome=outcome, actor_public_id=auth.actor_public_id,
            decision=decision)
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
        tenant_name=text(body, "organization"), login=text(body, "login"), password=password)
    response = JsonResponse(_login_json(result))
    response["Cache-Control"] = "no-store"
    return response


def _login_json(result: trust.LoginResult) -> dict[str, Any]:
    return {
        "token": result.token,
        "expires_at": result.expires_at.isoformat(),
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
        tenant_name=text(body, "organization"), login=text(body, "login"), password=password)
    response = JsonResponse(_login_json(result), status=201)
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_GET
def providers(request: HttpRequest) -> HttpResponse:
    """Which sign-in options this server offers (so the page shows only real ones)."""
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    return JsonResponse({"google": {"enabled": bool(client_id), "client_id": client_id or None}})


@endpoint("POST")
def logout(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Sign out everywhere: every token this person was ever issued stops working."""
    with tenant_transaction():
        trust.revoke_sessions(auth.actor_public_id, by=auth.actor_public_id)
    return JsonResponse({"signed_out": True})


# ------------------------------------------------------------------ staff screens
def _staff_only(auth: trust.Authenticated) -> str | None:
    """These endpoints serve the staff console: only operations-area accounts may use them,
    whatever else the policy table would allow. Returns the organization to check against."""
    area = home_for(auth.actor_type, list(auth.roles), auth.tenant_type)
    if area not in ("operations", "platform"):     # platform staff see their own tenant here
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return _catalog_org(auth)


def _evidence_block(auth: trust.Authenticated, org: str | None) -> dict[str, Any]:
    """Recent access decisions, audit entries and the chain check. Empty if not permitted."""
    if not is_allowed(auth, "read:evidence", organization_public_id=org):
        return {"evidence": False, "decisions": [], "events": [], "chain": None}
    with tenant_transaction():
        labels = trust.actor_labels()
        decisions = [
            {"at": d["at"], "actor": labels.get(str(d["actor"]), str(d["actor"])),
             "operation": d["operation"], "purpose": d["purpose"], "allowed": d["allowed"],
             "reason": d["reason"]}
            for d in policy.recent_decisions()
        ]
        events = [
            {**e, "actor": labels.get(e["actor"], e["actor"])} for e in evidence.recent_events()
        ]
        report = evidence.verify_chain()
    return {
        "evidence": True, "decisions": decisions, "events": events,
        "chain": {"ok": report.ok, "checked": report.checked, "problem": report.problem,
                  "bad_seq": report.first_bad_seq},
    }


@endpoint("GET")
def staff_dashboard(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    _authorize(auth, "read:analytics", organization_public_id=org)
    with tenant_transaction():
        numbers = trust.counts()
        attention = {
            "bookings_to_confirm": scheduling.booking_counts()["requested"],
            "family_requests": trust.pending_family_count(),
            "waiting_list": sum(waitlist.waiting_counts(org or "").values()),
            "messages": 0 if org is None else messaging.unread_threads_for_staff(org),
            "referrals_to_decide": 0 if org is None else sum(
                1 for r in referral.list_referrals(destination_organization_ref=org)
                if r["state"] == "issued"),
        }
        today = (scheduling.today_counts(org) if org else
                 {"confirmed": 0, "waiting": 0, "still_to_come": 0})
        target = trust.response_target_hours(org) if org else settings.RESPONSE_TARGET_HOURS
        late = {
            "hours": target,
            "bookings": scheduling.overdue_requests(target),
            "messages": 0 if org is None else messaging.overdue_threads_for_staff(org, target),
        }
    return JsonResponse({"tenant": auth.tenant_name, "counts": numbers, "attention": attention,
                         "late": late, "today": today, **_evidence_block(auth, org)})


def _work_json(rows: list[dict[str, Any]], labels: dict[str, str]) -> list[dict[str, Any]]:
    return [{"id": r["public_id"], "kind": r["kind"], "label": r["label"],
             "subject": r["subject_ref"], "created_at": r["created_at"], "due_at": r["due_at"],
             "status": r["status"], "closed_at": r["closed_at"],
             "overdue": r["overdue"], "assigned_to": r["assigned_to_ref"],
             "assigned_to_name": labels.get(r["assigned_to_ref"], "") if r["assigned_to_ref"]
             else ""} for r in rows]


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
        return JsonResponse({"me": auth.actor_public_id, "closed_days": days,
                             "items": _work_json(closed, labels)})
    with tenant_transaction():
        rows = workflow.list_items(
            org, assigned_to_ref=auth.actor_public_id if request.GET.get("mine") else "",
            unassigned=bool(request.GET.get("unassigned")))
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
    "opened": "Work arrived", "claimed": "Taken", "released": "Given back", "done": "Done",
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
    return JsonResponse({
        "chain_ok": data["chain_ok"], "status": data["status"],
        "events": [
            {"seq": e["seq"], "event": e["event_type"], "text": _WORK_WORDS[e["event_type"]],
             "who": ("Automatic" if e["actor_ref"] == "system"
                     else "The patient or family" if e["actor_ref"] == "patient"
                     else labels.get(e["actor_ref"], "Clinic staff")),
             "at": e["occurred_at"]}
            for e in data["events"]],
    })


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
    return JsonResponse({"days": 14, "can_manage": "R13" in auth.roles,
                         "bases": trust.NAME_CHECK_BASES, "providers": [
        {"id": ref, "name": labels.get(ref, ""), "name_check": checks.get(ref, {"state": "none"}),
         **load[ref]} for ref in refs]})


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
            provider_id, by_ref=auth.actor_public_id, organization_ref=org,
            basis=text(body, "basis"), note=text(body, "note", False))
    return JsonResponse({"name_check": done})


@endpoint("POST")
def staff_provider_unverify(
    request: HttpRequest, auth: trust.Authenticated, provider_id: str
) -> HttpResponse:
    org = _name_check_admin(auth)
    body = json_body(request)
    with tenant_transaction():
        done = trust.withdraw_check(
            provider_id, by_ref=auth.actor_public_id, organization_ref=org,
            reason=text(body, "reason"))
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
        hours = json_body(request).get("hours")
        with tenant_transaction():
            trust.set_response_target(org, hours, by=auth.actor_public_id)
    with tenant_transaction():
        recipients = len(trust.verified_emails_for_role(org, "R04"))
        target = trust.response_target_hours(org)
    return JsonResponse({
        "response_target_hours": target,
        "response_target_default": settings.RESPONSE_TARGET_HOURS,
        "can_set_target": "R13" in auth.roles,
        "reminder_lead_hours": settings.REMINDER_LEAD_HOURS,
        "waitlist_offer_hours": settings.WAITLIST_OFFER_HOURS,
        "late_email_recipients": recipients,
    })


@endpoint("GET")
def staff_compliance(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    _authorize(auth, "read:evidence", organization_public_id=org)
    with tenant_transaction():
        labels = trust.actor_labels()
        summary = policy.denial_summary()
    summary["by_person"] = [
        {**p, "actor": labels.get(str(p["actor"]), str(p["actor"]))}
        for p in summary["by_person"]]
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
    since, until = since.astimezone(dt.UTC), until.astimezone(dt.UTC)
    with tenant_transaction():
        bookings = scheduling.report(org, since, until, clock["zone"])
        people = trust.actor_labels()
        names = {s["public_id"]: s["name"] for s in catalog.list_services(active_only=False)}
        out = {
            "days": days, "from": since.astimezone(zone).date().isoformat(),
            "to": (until - dt.timedelta(seconds=1)).astimezone(zone).date().isoformat(),
            "zone": clock["zone"], "zone_source": clock["source"],
            "bookings": {**bookings, "busiest_services": [
                {"name": names.get(b["service_ref"], "A service"), "count": b["count"]}
                for b in bookings["busiest_services"]],
                "by_doctor": [
                    {**d, "name": people.get(d["provider_ref"], "A doctor") if d["provider_ref"]
                     else "No doctor chosen"} for d in bookings["by_doctor"]]},
            "forecast": scheduling.forecast_week(org),
            "referrals": referral.report(org, since, until),
            "waiting_list": waitlist.report(org, since, until),
            "messages": messaging.report(org, since, until),
        }
    return JsonResponse(out)


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
def platform_overview(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """One row per customer tenant with AGGREGATE numbers only: no patient rows, names or emails.
    Each view is recorded in the platform tenant's own audit chain."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    tenants = trust.list_tenants()
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id, action="platform.overview", object_type="platform",
            object_ref="overview", outcome=evidence.AuditOutcome.ALLOWED,
            details={"tenants": len(tenants)})
    with tenant_transaction():
        incidents = [i for i in trust.list_incidents() if i["status"] == "open"]
        waiting = [s for s in trust.list_changes() if s["status"] == "pending"]
        latest = trust.list_releases(limit=1)
    platform_work = {
        "open_incidents": len(incidents),
        # Open incidents that are SEV-0 or SEV-1 (the key keeps its older name "high").
        "high_incidents": sum(1 for i in incidents if i["severity"] in ("sev0", "sev1")),
        "suspensions_waiting": len(waiting),
        "latest_release": (
            {"version": latest[0]["version"], "released_on": latest[0]["released_on"]}
            if latest else None),
    }
    now = utc_now()
    since = now - dt.timedelta(hours=24)
    rows = []
    for t in tenants:
        with tenant_transaction(trust.context_for(t["public_id"])):
            rows.append({
                "id": t["public_id"], "name": t["name"], "type": t["type"], "status": t["status"],
                "self_registration": t["self_registration"] == "true",
                "created_at": t["created_at"],
                "counts": trust.counts(), "services": catalog.service_counts(),
                "bookings": scheduling.booking_counts(),
                "audit": evidence.activity_summary(since),
                "decisions": policy.decision_counts(since),
            })
    return JsonResponse({
        "cell": auth.context.cell_id, "generated_at": now.isoformat(),
        "outbox": backlog_stats(), "mail": mail_stats(), "work": platform_work, "tenants": rows,
    })


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
                version=body.get("version"), summary=body.get("summary"),
                details=body.get("details", ""), released_on=day,
                actor_public_id=auth.actor_public_id, decision=decision,
                organization_public_id=org)
        return JsonResponse({"release": made}, status=201)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        rows = trust.list_releases()
        labels = trust.actor_labels()
    return JsonResponse({"releases": [
        {"id": r["public_id"], "version": r["version"], "summary": r["summary"],
         "details": r["details"], "released_on": r["released_on"],
         "recorded_by": labels.get(r["recorded_by_ref"], "Zoiko staff")} for r in rows]})


def _incident_json(r: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    return {"id": r["public_id"], "title": r["title"], "severity": r["severity"],
            "happened_on": r["happened_on"], "details": r["details"], "status": r["status"],
            "resolution": r["resolution"], "resolved_on": r["resolved_on"],
            "recorded_by": labels.get(r["recorded_by_ref"], "Zoiko staff"),
            "resolved_by": labels.get(r["resolved_by_ref"], "Zoiko staff")
            if r["resolved_by_ref"] else ""}


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
                title=body.get("title"), severity=body.get("severity"), happened_on=day,
                details=body.get("details", ""), actor_public_id=auth.actor_public_id,
                decision=decision, organization_public_id=org)
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
    return JsonResponse({"me": auth.actor_public_id, "can_decide": "R12" in auth.roles,
                         "hazards": rows})


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
            hazard_id, actor_ref=auth.actor_public_id, severity=body.get("severity"),
            probability=body.get("probability"), basis_method=body.get("basis_method"),
            residual_risk=body.get("residual_risk"), owner_ref=auth.actor_public_id,
            hazardous_situation=body.get("hazardous_situation", ""),
            exposure_path=body.get("exposure_path", ""),
            affected_groups=body.get("affected_groups", ""),
            linked_build=body.get("linked_build", ""))
    return JsonResponse({"hazard": made})


@endpoint("POST")
def platform_hazard_review(
    request: HttpRequest, auth: trust.Authenticated, hazard_id: str
) -> HttpResponse:
    _safety_officer(auth)
    body = json_body(request)
    with tenant_transaction():
        made = safety.review(hazard_id, actor_ref=auth.actor_public_id,
                             verdict=body.get("verdict"), note=body.get("note", ""))
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
            incident_ref=incident_id, resolution=body.get("resolution"),
            actor_public_id=auth.actor_public_id, decision=decision, organization_public_id=org)
        labels = trust.actor_labels()
    return JsonResponse({"incident": _incident_json(made, labels)})


@endpoint("POST")
def platform_customer_sign_up(
    request: HttpRequest, auth: trust.Authenticated, customer_id: str
) -> HttpResponse:
    """Support opens or closes a customer's patient self-sign-up (`enabled`: true or false)."""
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    made = trust.set_customer_sign_up(
        customer_public_id=customer_id, enabled=body.get("enabled"),
        actor_public_id=auth.actor_public_id, platform_organization_public_id=org,
        decision=decision)
    return JsonResponse({"customer": made})


def _suspension_json(
    r: dict[str, Any], labels: dict[str, str], names: dict[str, str], me: str
) -> dict[str, Any]:
    return {"id": r["public_id"], "customer": r["customer_ref"],
            "customer_name": names.get(r["customer_ref"], "A customer"), "kind": r["kind"],
            "reason": r["reason"], "status": r["status"],
            "requested_by": labels.get(r["requested_by_ref"], "Zoiko staff"),
            "decided_by": labels.get(r["decided_by_ref"], "Zoiko staff")
            if r["decided_by_ref"] else "",
            "decision_note": r["decision_note"], "decided_at": r["decided_at"],
            "created_at": r["created_at"], "mine": r["requested_by_ref"] == me}


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
    made = trust.request_change(
        customer_public_id=customer_id, kind=body.get("action"), reason=body.get("reason"),
        actor_public_id=auth.actor_public_id, platform_organization_public_id=org,
        decision=decision)
    return JsonResponse({"id": made["public_id"], "status": made["status"]}, status=201)


def _decide_suspension(
    request: HttpRequest, auth: trust.Authenticated, suspension_id: str, approve: bool
) -> HttpResponse:
    org = _platform_only(auth)
    body = json_body(request)
    decision = _authorize(auth, "write:platform", organization_public_id=org)
    made = trust.decide_change(
        request_ref=suspension_id, approve=approve, note=body.get("note", ""),
        actor_public_id=auth.actor_public_id, platform_organization_public_id=org,
        decision=decision)
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
        request_ref=suspension_id, actor_public_id=auth.actor_public_id,
        platform_organization_public_id=org, decision=decision)
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
        customer_name=text(body, "customer"), site_name=text(body, "site"),
        admin_email=text(body, "admin_email"), actor_public_id=auth.actor_public_id,
        platform_organization_public_id=org, decision=decision)
    return JsonResponse({"customer": made}, status=201)


# ------------------------------------------------------------------ notifications (patients)
def _notification_json(n: dict[str, Any]) -> dict[str, Any]:
    return {"id": n["public_id"], "person": n["person_ref"], "kind": n["kind"], "title": n["title"],
            "body": n["body"], "booking": n["booking_ref"], "created_at": n["created_at"],
            "read": n["read_at"] is not None}


@endpoint("GET")
def notifications(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Notifications for every patient this login may read (own record, or a guardian's grant)."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    people = [p for p in reachable if is_allowed(auth, "read:comms", subject_ref=p)]
    with tenant_transaction():
        items = comms.list_for_persons(people)
        unread = comms.unread_count(people)
    return JsonResponse({"notifications": [_notification_json(n) for n in items],
                         "unread": unread})


@endpoint("POST")
def notification_read(
    request: HttpRequest, auth: trust.Authenticated, notification_id: str
) -> HttpResponse:
    with tenant_transaction():
        person = comms.get_notification(notification_id)["person_ref"]
    decision = _authorize(auth, "write:comms", subject_ref=person)
    with tenant_transaction():
        done = comms.mark_read(
            notification_ref=notification_id, actor_public_id=auth.actor_public_id,
            decision=decision)
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
    trust.request_password_reset(
        tenant_name=text(body, "organization"), login=text(body, "login"))
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
            auth.actor_public_id, current_password=current, new_password=new)
    if not result.ok or result.expires_at is None:      # raised AFTER the transaction, so the
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["current_password"])   # counter stays
    return _no_store(JsonResponse(
        {"token": result.token, "expires_at": result.expires_at.isoformat()}))


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
        result = trust.set_email(
            auth.actor_public_id, new_email=email, current_password=current)
        status = trust.email_status(auth.actor_public_id)
    if not result.ok:                       # raised AFTER the transaction, so the counter is kept
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
    with tenant_transaction():
        member = trust.invite_staff(
            email=text(body, "email"), role_code=text(body, "role"),
            organization_public_id=str(org), actor_public_id=auth.actor_public_id,
            decision=decision)
    return JsonResponse({"member": member}, status=201)


@endpoint("POST")
def staff_member_status(
    request: HttpRequest, auth: trust.Authenticated, member_id: str
) -> HttpResponse:
    body = json_body(request)
    org = _staff_only(auth)
    decision = _authorize(auth, "write:staff", organization_public_id=org)
    with tenant_transaction():
        done = trust.set_member_status(
            member_id, text(body, "status"), organization_public_id=str(org),
            actor_public_id=auth.actor_public_id, decision=decision)
    return JsonResponse({"member": done})


@endpoint("POST")
def staff_member_resend(
    request: HttpRequest, auth: trust.Authenticated, member_id: str
) -> HttpResponse:
    org = _staff_only(auth)
    decision = _authorize(auth, "write:staff", organization_public_id=org)
    with tenant_transaction():
        trust.resend_invitation(
            member_id, organization_public_id=str(org), actor_public_id=auth.actor_public_id,
            decision=decision)
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
        out = [_booking_json(r, svc, loc, orgs, labels, checked)
               for r in rows if r["person_ref"] in allowed]
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
        {"id": link["person_ref"], "basis": link["basis"], "since": link["since"],
         "appointments": count.get(link["person_ref"], 0)}
        for link in links if is_allowed(auth, "read:person", subject_ref=link["person_ref"])
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
        enabled = json_body(request).get("enabled")
        if not isinstance(enabled, bool):
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["enabled"])
        with tenant_transaction():
            comms.set_email_notices(auth.actor_public_id, enabled)
    with tenant_transaction():
        persons = trust.reachable_persons(auth.actor_public_id)
        own = [p for p in persons if (trust.verified_login_email_of_person(p) or ("",))[0]
               == auth.actor_public_id]
        return JsonResponse({
            "enabled": comms.email_notices_enabled(auth.actor_public_id),
            "can_email": bool(own),
        })


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
        "exported_at": utc_now().isoformat(), "format": "zoiko-access-export-1",
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
            conversations.append({**t, "messages": [
                {"side": m["side"], "at": m["at"], "body": m["body"]} for m in messages]})
    out["conversations"] = conversations
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id, action="data.export", object_type="actor",
            object_ref=auth.actor_public_id, outcome=evidence.AuditOutcome.ALLOWED)
    response = JsonResponse(out, json_dumps_params={"indent": 2})
    response["Content-Disposition"] = 'attachment; filename="my-zoiko-data.json"'
    return response


@endpoint("GET")
def my_care_team(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Who is treating each patient this login may read."""
    people = _my_people(auth, "read:person")
    with tenant_transaction():
        labels = trust.actor_labels()
        out = [{"person": p, "team": [
            {"clinician": labels.get(m["actor_ref"], ""), "basis": m["basis"],
             "since": m["since"], "until": m["until"] or None}
            for m in trust.care_team_of(p)]} for p in people]
    return JsonResponse({"people": out})


@endpoint("GET")
def my_access_log(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Who looked at (or tried to look at) the information of each patient this login may read,
    newest first. The patient's own looks are left out: this is about OTHER people."""
    people = _my_people(auth, "read:evidence")
    with tenant_transaction():
        labels = trust.actor_labels()
        rows = policy.decisions_about(people, exclude_actors=[auth.actor_public_id], limit=100)
    return JsonResponse({"entries": [
        {"at": r["at"], "who": labels.get(str(r["actor"]), "The system"),
         "role": r["role"] or None, "operation": r["operation"], "purpose": r["purpose"],
         "allowed": r["allowed"], "person": r["subject"]}
        for r in rows]})


# ------------------------------------------------------------------ messages
def _is_staff(auth: trust.Authenticated) -> bool:
    return home_for(auth.actor_type, list(auth.roles), auth.tenant_type) == "operations"


def _thread_json(t: dict[str, Any], staff: bool) -> dict[str, Any]:
    return {
        "id": t["public_id"], "subject": t["subject"], "status": t["status"],
        "patient": t["person_ref"], "last_message_at": t["last_message_at"],
        "unread": t["staff_unread"] if staff else t["patient_unread"],
        # What THIS login has not opened (others' replies or openings never clear it for them).
        "unread_for_me": t.get("unread_for_me",
                               t["staff_unread"] if staff else t["patient_unread"]),
    }


@endpoint("GET", "POST")
def message_threads(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the threads this login may see (a clinic: its own; a patient: their people's).
    POST: a patient or guardian writes to their clinic."""
    org = _catalog_org(auth)
    staff = _is_staff(auth)
    if request.method == "POST":
        require_feature("messaging.secure")
        body = json_body(request)
        if staff or org is None or not set(auth.roles) & {"R01", "R02"}:
            raise ZoikoError(ErrorCode.POLICY_DENIED)   # only patients and guardians start threads
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
            made = messaging.start_thread(
                person_ref=person, organization_ref=org, subject=text(body, "subject"),
                body=text(body, "body"), actor_public_id=auth.actor_public_id, decision=decision)
        return JsonResponse({"thread": _thread_json(made, False)}, status=201)
    if staff:
        _staff_only(auth)
        if org is None or not is_allowed(auth, "read:comms", organization_public_id=org):
            return JsonResponse({"threads": []})
        with tenant_transaction():
            rows = messaging.list_threads(
                organization_ref=org, viewer_ref=auth.actor_public_id, viewer_side="staff")
    else:
        with tenant_transaction():
            reachable = trust.reachable_persons(auth.actor_public_id)
        persons = [p for p in reachable if is_allowed(auth, "read:comms", subject_ref=p)]
        with tenant_transaction():
            rows = messaging.list_threads(
                person_refs=persons, viewer_ref=auth.actor_public_id,
                viewer_side="patient") if persons else []
    out = [_thread_json(t, staff) for t in rows]
    if staff:
        _add_names(auth, out, "patient")          # staff see the name where allowed
    return JsonResponse({"threads": out})


def _thread_access(
    auth: trust.Authenticated, thread_id: str, operation: str
) -> tuple[dict[str, Any], str | None, policy.Decision]:
    org = _catalog_org(auth)
    with tenant_transaction():
        thread = messaging.get_thread(thread_id)
    if _is_staff(auth) and thread["organization_ref"] != org:
        raise ZoikoError(ErrorCode.NOT_FOUND)             # another organization's thread
    decision = _authorize(auth, operation, organization_public_id=org,
                          subject_ref=thread["person_ref"])
    return thread, org, decision


@endpoint("GET")
def message_thread(
    request: HttpRequest, auth: trust.Authenticated, thread_id: str
) -> HttpResponse:
    """One thread with its messages. Opening it marks it read for the side that opened it."""
    staff = _is_staff(auth)
    _thread_access(auth, thread_id, "read:comms")
    with tenant_transaction():
        opened = messaging.open_thread(
            thread_id, side="staff" if staff else "patient", viewer_ref=auth.actor_public_id)
        labels = trust.actor_labels()
    out = _thread_json(opened["thread"], staff)
    if staff:
        _add_names(auth, [out], "patient")
    out["messages"] = [
        {"id": m["public_id"], "side": m["side"], "body": m["body"], "at": m["at"],
         "mine": m["author_ref"] == auth.actor_public_id,
         # When the OTHER side first opened a message written by this reader's own side. It says
         # a person opened it: not that it was understood, and never a clinical review.
         "seen_at": m["seen_at"] if m["side"] == ("staff" if staff else "patient") else None,
         "from": labels.get(m["author_ref"], "") if m["side"] == "staff" else ""}
        for m in opened["messages"]]
    return JsonResponse({"thread": out})


@endpoint("POST")
def message_reply(
    request: HttpRequest, auth: trust.Authenticated, thread_id: str
) -> HttpResponse:
    require_feature("messaging.secure")
    body = json_body(request)
    _, org, decision = _thread_access(auth, thread_id, "write:comms")
    with tenant_transaction():
        done = messaging.reply(
            thread_ref=thread_id, body=text(body, "body"), actor_public_id=auth.actor_public_id,
            decision=decision, organization_public_id=org)
    return JsonResponse({"thread": _thread_json(done, _is_staff(auth))})


@endpoint("POST")
def message_close(
    request: HttpRequest, auth: trust.Authenticated, thread_id: str
) -> HttpResponse:
    org = _staff_only(auth)
    _, _, decision = _thread_access(auth, thread_id, "write:comms")
    with tenant_transaction():
        done = messaging.close_thread(
            thread_ref=thread_id, actor_public_id=auth.actor_public_id, decision=decision,
            organization_public_id=org or "")
    return JsonResponse({"thread": _thread_json(done, True)})


# ------------------------------------------------------------------ family members
def _family_json(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": g["public_id"], "status": g["status"], "relationship": g["relationship"],
        "person": g["person_ref"], "name": g["name"], "birth_date": g["birth_date"],
        "scopes": g.get("scopes", []),
        "valid_until": g["valid_until"], "requested_at": g["created_at"],
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
        raise ZoikoError(ErrorCode.POLICY_DENIED)    # only a patient with their own record
    decision = _authorize(auth, "write:person", organization_public_id=org, subject_ref=own)
    with tenant_transaction():
        made = trust.request_family_member(
            requester_public_id=auth.actor_public_id, own_person_ref=own,
            relationship=text(body, "relationship"), name=text(body, "name"),
            birth_date=text(body, "birth_date"), decision=decision, organization_public_id=org)
    return JsonResponse({"request": _family_json(made)}, status=201)


@endpoint("POST")
def family_withdraw(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str
) -> HttpResponse:
    """Withdraw a waiting request, or stop acting for someone. Only your own."""
    with tenant_transaction():
        done = trust.withdraw_family_member(
            grant_ref=grant_id, requester_public_id=auth.actor_public_id)
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
        return {**_family_json(g), "requested_by": labels.get(g["delegate_ref"], ""),
                "requested_by_name": names.get(owns.get(g["delegate_ref"], "")) or None}

    return JsonResponse({"requests": [shown(g) for g in rows],
                         "approved": [shown(g) for g in approved]})


def _decide_family(
    request: HttpRequest, auth: trust.Authenticated, grant_id: str, approve: bool
) -> HttpResponse:
    org = _staff_only(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    body = json_body(request)
    with tenant_transaction():
        grant = trust.get_family_grant(grant_id)
    decision = _authorize(auth, "write:person", organization_public_id=org,
                          subject_ref=grant["person_ref"])
    with tenant_transaction():
        if approve:
            done = trust.approve_family_member(
                grant_ref=grant_id, approver_public_id=auth.actor_public_id, decision=decision,
                organization_public_id=org, verified=body.get("verified") is True,
                scopes=[s for s in body.get("scopes", []) if isinstance(s, str)]
                if isinstance(body.get("scopes"), list) else None,
                valid_until=text(body, "valid_until", False))
        else:
            done = trust.reject_family_member(
                grant_ref=grant_id, approver_public_id=auth.actor_public_id, decision=decision,
                organization_public_id=org)
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
    decision = _authorize(auth, "write:person", organization_public_id=org,
                          subject_ref=grant["person_ref"])
    me = auth.actor_public_id
    with tenant_transaction():
        if step == "proof-needed":
            done = trust.ask_for_proof(
                grant_ref=grant_id, approver_public_id=me, decision=decision,
                organization_public_id=org)
        elif step == "proof-received":
            done = trust.mark_proof_received(
                grant_ref=grant_id, approver_public_id=me, decision=decision,
                organization_public_id=org)
        elif step == "suspend":
            done = trust.suspend_family_member(
                grant_ref=grant_id, approver_public_id=me, decision=decision,
                organization_public_id=org, reason=text(body, "reason", False))
        else:
            done = trust.resume_family_member(
                grant_ref=grant_id, approver_public_id=me, decision=decision,
                organization_public_id=org)
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
    return JsonResponse({"profiles": [
        {"person": p, "own": p == own, "editable": p in editable,
         "name": found.get(p, {}).get("name", ""),
         "birth_date": found.get(p, {}).get("birth_date", ""),
         "phone": found.get(p, {}).get("phone", ""),
         # Whether a clinic has checked who this patient is. Never whether a possible duplicate is
         # being reviewed: a patient is not told while it is unresolved (GHE-06).
         "identity": {
             "status": identity[p]["status"], "checked_on": identity[p]["checked_on"],
             "valid_until": identity[p]["valid_until"],
             "clinic": orgs.get(identity[p]["clinic_ref"], ""), "why_not": identity[p]["why_not"]}}
        for p in persons]})


@endpoint("GET", "POST")
def person_profile(
    request: HttpRequest, auth: trust.Authenticated, person_id: str
) -> HttpResponse:
    """GET: one patient's details, if this login may read that patient. POST: save them (the
    patient themselves, or clinic staff for their patient)."""
    org = _catalog_org(auth)
    if request.method == "GET":
        _authorize(auth, "read:person", organization_public_id=org, subject_ref=person_id)
        with tenant_transaction():
            found = trust.get_person_profiles([person_id]).get(person_id, {})
        return JsonResponse({"profile": {
            "person": person_id, "name": found.get("name", ""),
            "birth_date": found.get("birth_date", ""), "phone": found.get("phone", "")}})
    body = json_body(request)
    decision = _authorize(auth, "write:person", organization_public_id=org, subject_ref=person_id)
    with tenant_transaction():
        saved = trust.set_person_profile(
            person_id, legal_name=text(body, "name"), birth_date=text(body, "birth_date", False),
            phone=text(body, "phone", False), actor_public_id=auth.actor_public_id,
            decision=decision, organization_public_id=org)
    return JsonResponse({"profile": {"person": person_id, "name": saved["legal_name"],
                                     "birth_date": saved["birth_date"], "phone": saved["phone"]}})


# ------------------------------------------------------------------ insurance coverage
def _coverage_json(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c["public_id"], "person": c["person_ref"], "insurer": c["insurer_name"],
        "plan": c["plan_name"], "member_last4": c["member_last4"],
        "valid_from": c["valid_from"], "valid_until": c["valid_until"], "status": c["status"],
        "in_force": c["in_force"], "added_at": c["created_at"],
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
        decision = _authorize(auth, "write:coverage", organization_public_id=org,
                              subject_ref=person)
        with tenant_transaction():
            made = coverage.add_coverage(
                person_ref=person, insurer_name=text(body, "insurer"),
                plan_name=text(body, "plan", False), member_id=text(body, "member_id"),
                valid_from=text(body, "valid_from", False),
                valid_until=text(body, "valid_until", False),
                actor_public_id=auth.actor_public_id, decision=decision,
                organization_public_id=org)
        return JsonResponse({"coverage": _coverage_json(made)}, status=201)
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:coverage", subject_ref=p)]
    with tenant_transaction():
        rows = coverage.list_coverage(persons)
    return JsonResponse({"coverage": [_coverage_json(c) for c in rows]})


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
            if shared else []
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
        if person not in reachable:        # staff may record insurance, never decide who sees it
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        decision = _authorize(auth, "write:coverage", organization_public_id=org,
                              subject_ref=person)
        with tenant_transaction():
            done = coverage.set_sharing(
                person_ref=person, share=share, actor_public_id=auth.actor_public_id,
                decision=decision, organization_public_id=org)
        return JsonResponse({"sharing": done})
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:coverage", subject_ref=p)]
    with tenant_transaction():
        chosen = coverage.shared_with_staff(persons)
    return JsonResponse({"sharing": [
        {"person": p, "share_with_staff": chosen[p]} for p in persons]})


@endpoint("POST")
def coverage_end(
    request: HttpRequest, auth: trust.Authenticated, coverage_id: str
) -> HttpResponse:
    org = _catalog_org(auth)
    with tenant_transaction():
        current = coverage.get_coverage(coverage_id)
    decision = _authorize(auth, "write:coverage", organization_public_id=org,
                          subject_ref=current["person_ref"])
    with tenant_transaction():
        done = coverage.end_coverage(
            coverage_ref=coverage_id, actor_public_id=auth.actor_public_id, decision=decision,
            organization_public_id=org)
        person = current["person_ref"]
        own = person in trust.reachable_persons(auth.actor_public_id)
        shared = own or coverage.shared_with_staff([person])[person]
    if not shared:        # staff may remove it, but may not read it: no details in the answer
        return JsonResponse({"coverage": {"id": done["public_id"], "status": done["status"]}})
    return JsonResponse({"coverage": _coverage_json(done)})


# ------------------------------------------------------------------ waiting list
def _waitlist_json(
    rows: list[dict[str, Any]], auth: trust.Authenticated
) -> list[dict[str, Any]]:
    with tenant_transaction():
        orgs = trust.organization_names()
        cache: dict[str, Any] = {}
        out = []
        for e in rows:
            service = cache.setdefault(e["service_ref"], catalog.get_service(e["service_ref"]))
            out.append({
                "id": e["public_id"], "state": e["state"], "patient": e["person_ref"],
                "joined_at": e["created_at"], "offered_at": e["offered_at"],
                "service": {"id": service["public_id"], "name": service["name"]},
                "organization": {"id": e["organization_ref"],
                                 "name": orgs.get(e["organization_ref"], "")},
            })
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
        decision = _authorize(auth, "write:booking",
                              organization_public_id=service["organization_ref"],
                              subject_ref=person)
        with tenant_transaction():
            made = waitlist.join(person_ref=person, service_ref=service_ref,
                                 actor_public_id=auth.actor_public_id, decision=decision)
        return JsonResponse({"entry": _waitlist_json([made], auth)[0]}, status=201)
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:booking", subject_ref=p)]
    rows: list[dict[str, Any]] = []
    if persons:
        with tenant_transaction():
            rows = waitlist.list_entries(person_refs=persons)
    return JsonResponse({"entries": _waitlist_json(rows, auth)})


@endpoint("GET")
def staff_waitlist(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Everyone waiting for this organization's services, in queue order, with their place."""
    _staff_only(auth)
    rows: list[dict[str, Any]] = []
    for org in held_organizations(auth):
        if is_allowed(auth, "read:booking", organization_public_id=org):
            with tenant_transaction():
                rows += waitlist.list_entries(organization_ref=org)
    rows.sort(key=lambda e: e["created_at"])                     # longest waiting first
    place: dict[str, int] = {}
    entries = _waitlist_json(rows, auth)
    with tenant_transaction():
        journeys = workflow.instances_for([e["id"] for e in entries], "J3.waitlist")
    for item in entries:
        place[item["service"]["id"]] = place.get(item["service"]["id"], 0) + 1
        item["place"] = place[item["service"]["id"]]
        # Where it stands in its journey (none for a request that joined before journeys).
        item["journey"] = journeys[item["id"]]["state"] if item["id"] in journeys else None
    return JsonResponse({"entries": entries})


@endpoint("POST")
def waitlist_leave(
    request: HttpRequest, auth: trust.Authenticated, entry_id: str
) -> HttpResponse:
    with tenant_transaction():
        entry = waitlist.get_entry(entry_id)
    decision = _authorize(auth, "write:booking",
                          organization_public_id=entry["organization_ref"],
                          subject_ref=entry["person_ref"])
    with tenant_transaction():
        done = waitlist.leave(entry_ref=entry_id, actor_public_id=auth.actor_public_id,
                              decision=decision)
    return JsonResponse({"entry": _waitlist_json([done], auth)[0]})


# ------------------------------------------------------------------ referrals
def _referral_json(
    r: dict[str, Any], services: dict[str, Any], orgs: dict[str, str], labels: dict[str, str],
) -> dict[str, Any]:
    service = services.setdefault(r["service_ref"], catalog.get_service(r["service_ref"]))
    dest = r["destination_organization_ref"]
    return {
        "id": r["public_id"], "patient": r["person_ref"], "state": r["state"],
        "urgency": r["urgency"], "reason": r["reason"],
        "decision_reason": r["decision_reason"], "decided_at": r["decided_at"],
        "created_at": r["created_at"],
        "acknowledged_at": r["acknowledged_at"], "escalated": r["escalated"],
        "owner": ({"id": r["owner_ref"], "name": labels.get(r["owner_ref"], "")}
                  if r["owner_ref"] else None),
        "responsible": {"id": r["responsible_ref"],
                        "name": labels.get(r["responsible_ref"], "")},
        "referrer": {"id": r["referrer_ref"], "name": labels.get(r["referrer_ref"], "")},
        "from_organization": {"id": r["from_organization_ref"],
                              "name": orgs.get(r["from_organization_ref"], "")},
        "service": {"id": service["public_id"], "name": service["name"]},
        "organization": {"id": dest, "name": orgs.get(dest, "")},
    }


def _referrals_out(
    rows: list[dict[str, Any]], auth: trust.Authenticated
) -> list[dict[str, Any]]:
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
        with tenant_transaction():
            rows = referral.list_referrals(referrer_ref=auth.actor_public_id)
        allowed = {p for p in sorted({r["person_ref"] for r in rows})
                   if is_allowed(auth, "read:referral", subject_ref=p)}
        return JsonResponse({"referrals": _referrals_out(
            [r for r in rows if r["person_ref"] in allowed], auth)})
    require_feature("referral.exchange")
    body = json_body(request)
    person, service = text(body, "patient"), text(body, "service")
    org = _catalog_org(auth)
    if org is None:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    decision = _authorize(auth, "write:referral", organization_public_id=org, subject_ref=person)
    with tenant_transaction():
        made = referral.issue(
            person_ref=person, service_ref=service, reason=text(body, "reason"),
            urgency=text(body, "urgency", required=False) or "routine",
            actor_public_id=auth.actor_public_id, from_organization_ref=org, decision=decision)
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
    rows = _referrals_out(list(seen.values()), auth)
    with tenant_transaction():
        journeys = workflow.instances_for([r["id"] for r in rows], "J2.referral")
    for row in rows:                  # where each one stands in its journey (none before journeys)
        row["journey"] = journeys[row["id"]]["state"] if row["id"] in journeys else None
    return JsonResponse({"referrals": rows})


@endpoint("POST")
def referral_decide(
    request: HttpRequest, auth: trust.Authenticated, referral_id: str
) -> HttpResponse:
    """Accept, ask for more, or decline. Only the named owner of the receiving inbox, or the
    fallback, who is a doctor (the domain re-checks all of this)."""
    _clinician_only(auth)
    require_feature("referral.exchange")
    body = json_body(request)
    with tenant_transaction():
        current = referral.get_referral(referral_id)
    decision = _authorize(
        auth, "write:referral", organization_public_id=current["destination_organization_ref"],
        subject_ref=current["person_ref"])
    with tenant_transaction():
        done = referral.decide(
            referral_ref=referral_id, outcome=text(body, "decision"),
            reason=text(body, "reason", required=False),
            actor_public_id=auth.actor_public_id, decision=decision)
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
        auth, "write:referral", organization_public_id=current["destination_organization_ref"],
        subject_ref=current["person_ref"])
    with tenant_transaction():
        done = referral.acknowledge(
            referral_ref=referral_id, actor_public_id=auth.actor_public_id, decision=decision)
    return JsonResponse({"referral": _referrals_out([done], auth)[0]})


@endpoint("GET")
def clinician_referral_inbox(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Referrals this doctor is named to answer, as owner or as the fallback."""
    _clinician_only(auth)
    with tenant_transaction():
        rows = referral.list_for_answerer(auth.actor_public_id)
    allowed = {p for p in sorted({r["person_ref"] for r in rows})
               if is_allowed(auth, "read:referral", subject_ref=p)}
    return JsonResponse({"referrals": _referrals_out(
        [r for r in rows if r["person_ref"] in allowed], auth)})


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
                organization_ref=org, owner_ref=text(body, "owner"),
                fallback_ref=text(body, "fallback", required=False),
                actor_public_id=auth.actor_public_id, decision=decision)
    else:
        _authorize(auth, "read:referral", organization_public_id=org)
    with tenant_transaction():
        box = referral.inbox_dict(org)
        labels = trust.actor_labels()
        doctors = trust.clinicians_at(org)

    def who(ref: str) -> dict[str, str] | None:
        return {"id": ref, "name": labels.get(ref, "")} if ref else None

    return JsonResponse({
        "owner": who(box["owner_ref"]) if box else None,
        "fallback": who(box["fallback_ref"]) if box else None,
        "doctors": [{"id": d, "name": labels.get(d, "")} for d in doctors],
        "can_manage": "R13" in auth.roles,
    })


@endpoint("GET")
def my_referrals(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The patient's (or guardian's) referrals: only for people they may read."""
    with tenant_transaction():
        reachable = trust.reachable_persons(auth.actor_public_id)
    persons = [p for p in reachable if is_allowed(auth, "read:referral", subject_ref=p)]
    rows: list[dict[str, Any]] = []
    if persons:
        with tenant_transaction():
            rows = referral.list_referrals(person_refs=persons)
    return JsonResponse({"referrals": _referrals_out(rows, auth)})


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
                feature_id=text(body, "feature_id"), purpose=text(body, "purpose"),
                lane=text(body, "lane"), level=text(body, "level"),
                model_digest=ai.digest(model), prompt_digest=ai.digest(prompt),
                allowed_tools=tools, created_by_ref=auth.actor_public_id,
                clinical_owner_ref=text(body, "clinical_owner", False))
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
            permit_id, text(body, "to"), actor_ref=auth.actor_public_id,
            reason=text(body, "reason", False), expiry=expiry)
    return JsonResponse({"permit": done})
