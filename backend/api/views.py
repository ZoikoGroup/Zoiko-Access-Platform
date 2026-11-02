"""The /api/v1 endpoints used by the web apps.

Each one: logged-in actor -> tenant bound -> policy decision (recorded, even when denied) ->
domain function -> JSON. Errors come out as problem+json from the middleware.
"""
from __future__ import annotations

import datetime as dt
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
from foundation.timeutil import utc_now
from modules.catalog import api as catalog
from modules.comms import api as comms
from modules.coverage import api as coverage
from modules.evidence import api as evidence
from modules.messaging import api as messaging
from modules.policy import api as policy
from modules.referral import api as referral
from modules.scheduling import api as scheduling
from modules.trust import api as trust
from modules.waitlist import api as waitlist

from .support import (
    endpoint,
    held_organizations,
    home_for,
    is_allowed,
    json_body,
    purpose_for,
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


def _provider_json(slot: dict[str, Any], labels: dict[str, str] | None) -> dict[str, str] | None:
    """The doctor on a time, shown by login (names are not stored yet)."""
    ref = slot.get("provider_ref")
    if not ref:
        return None
    names = labels if labels is not None else trust.actor_labels()
    return {"id": ref, "name": names.get(ref, "")}


def _booking_json(
    booking: dict[str, Any], services: dict[str, Any], locations: dict[str, Any],
    orgs: dict[str, str], labels: dict[str, str] | None = None,
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
                     "address": location["address"]},
        "starts_at": slot["starts_at"], "ends_at": slot["ends_at"],
        "local_start": slot["local_start"], "local_zone": slot["local_zone"],
        "provider": _provider_json(slot, labels),
        "rescheduled_from": booking.get("rescheduled_from_ref") or None,
        "attendance": booking.get("attendance") or None,
        "late": _waiting_too_long(booking),
    }


def _waiting_too_long(booking: dict[str, Any]) -> bool:
    """A booking still waiting for confirmation after the clinic's response target."""
    if booking["status"] != "requested":
        return False
    waited = utc_now() - dt.datetime.fromisoformat(booking["created_at"])
    return waited > dt.timedelta(hours=settings.RESPONSE_TARGET_HOURS)


@endpoint("GET")
def me(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    with tenant_transaction():
        orgs = trust.organization_names()
        roles = trust.active_roles(auth.actor_public_id)
        persons = trust.reachable_persons(auth.actor_public_id)
        email = trust.email_status(auth.actor_public_id)
    return JsonResponse({
        "actor": auth.actor_public_id, "type": auth.actor_type, "tenant": auth.tenant_name,
        "cell": auth.context.cell_id, "purpose": purpose_for(auth), "persons": persons,
        "home": home_for(auth.actor_type, [r for r, _ in roles], auth.tenant_type),
        "email": email,
        "roles": [{"role": r, "organization": {"id": o, "name": orgs.get(o, "")}}
                  for r, o in roles],
    })


def _service_json(s: dict[str, Any], orgs: dict[str, str]) -> dict[str, Any]:
    upcoming = scheduling.list_open_slots(s["public_id"], limit=1)
    return {
        "id": s["public_id"], "name": s["name"], "category": s["category"],
        "description": s["description"], "duration_minutes": s["duration_minutes"],
        "mode": s["mode"], "status": s["status"],
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
            return JsonResponse({"services": [
                {**_service_json(s, orgs), "waiting": waiting.get(s["public_id"], 0)}
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
                address=text(body, "address", False),
                actor_public_id=auth.actor_public_id, decision=decision)
        return JsonResponse({"location": _location_json(row)}, status=201)
    wanted = request.GET.get("organization") or _catalog_org(auth)
    _authorize(auth, "read:catalog", organization_public_id=wanted)
    with tenant_transaction():
        rows = catalog.list_locations(organization_ref=wanted or "")
    return JsonResponse({"locations": [_location_json(r) for r in rows]})


def _location_json(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["public_id"], "name": row["name"], "address": row["address"],
            "time_zone": row["time_zone"], "organization": row["organization_ref"]}


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
            actor_public_id=auth.actor_public_id, decision=decision)
    return JsonResponse({"created": len(made)}, status=201)


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
    return JsonResponse({
        "service": {"id": service["public_id"], "name": service["name"],
                    "duration_minutes": service["duration_minutes"]},
        "slots": [{"id": s["public_id"], "starts_at": s["starts_at"], "ends_at": s["ends_at"],
                   "local_start": s["local_start"], "local_zone": s["local_zone"],
                   "location": names[s["location_ref"]],
                   "provider": _provider_json(s, people)} for s in slots],
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
        rows = [_booking_json(b, svc, loc, orgs, names) for b in seen.values()]
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
        action = scheduling.confirm if verb == "confirm" else scheduling.cancel
        done = action(booking_ref=booking_id, actor_public_id=auth.actor_public_id,
                      decision=decision)
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
        target = settings.RESPONSE_TARGET_HOURS
        late = {
            "hours": target,
            "bookings": scheduling.overdue_requests(target),
            "messages": 0 if org is None else messaging.overdue_threads_for_staff(org, target),
        }
    return JsonResponse({"tenant": auth.tenant_name, "counts": numbers, "attention": attention,
                         "late": late, **_evidence_block(auth, org)})


@endpoint("GET")
def staff_compliance(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    org = _staff_only(auth)
    _authorize(auth, "read:evidence", organization_public_id=org)
    return JsonResponse(_evidence_block(auth, org))


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
    until = utc_now()
    since = (until - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    until = (until + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    with tenant_transaction():
        bookings = scheduling.report(org, since, until)
        names = {s["public_id"]: s["name"] for s in catalog.list_services(active_only=False)}
        out = {
            "days": days, "from": since.date().isoformat(),
            "to": (until - dt.timedelta(seconds=1)).date().isoformat(),
            "bookings": {**bookings, "busiest_services": [
                {"name": names.get(b["service_ref"], "A service"), "count": b["count"]}
                for b in bookings["busiest_services"]]},
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
        "outbox": backlog_stats(), "mail": mail_stats(), "tenants": rows,
    })


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
        out = [_booking_json(r, svc, loc, orgs, labels) for r in rows if r["person_ref"] in allowed]
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
    }


@endpoint("GET", "POST")
def message_threads(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """GET: the threads this login may see (a clinic: its own; a patient: their people's).
    POST: a patient or guardian writes to their clinic."""
    org = _catalog_org(auth)
    staff = _is_staff(auth)
    if request.method == "POST":
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
            rows = messaging.list_threads(organization_ref=org)
    else:
        with tenant_transaction():
            reachable = trust.reachable_persons(auth.actor_public_id)
        persons = [p for p in reachable if is_allowed(auth, "read:comms", subject_ref=p)]
        with tenant_transaction():
            rows = messaging.list_threads(person_refs=persons) if persons else []
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
        opened = messaging.open_thread(thread_id, side="staff" if staff else "patient")
        labels = trust.actor_labels()
    out = _thread_json(opened["thread"], staff)
    if staff:
        _add_names(auth, [out], "patient")
    out["messages"] = [
        {"id": m["public_id"], "side": m["side"], "body": m["body"], "at": m["at"],
         "mine": m["author_ref"] == auth.actor_public_id,
         "from": labels.get(m["author_ref"], "") if m["side"] == "staff" else ""}
        for m in opened["messages"]]
    return JsonResponse({"thread": out})


@endpoint("POST")
def message_reply(
    request: HttpRequest, auth: trust.Authenticated, thread_id: str
) -> HttpResponse:
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
    names = _names(auth, {p for p in owns.values() if p})
    return JsonResponse({"requests": [
        {**_family_json(g), "requested_by": labels.get(g["delegate_ref"], ""),
         "requested_by_name": names.get(owns.get(g["delegate_ref"], "")) or None}
        for g in rows]})


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
    return JsonResponse({"profiles": [
        {"person": p, "own": p == own, "editable": p in editable,
         "name": found.get(p, {}).get("name", ""),
         "birth_date": found.get(p, {}).get("birth_date", ""),
         "phone": found.get(p, {}).get("phone", "")}
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
    """One patient's insurance (for clinic staff, or the patient): needs `read:coverage`."""
    org = _catalog_org(auth)
    _authorize(auth, "read:coverage", organization_public_id=org, subject_ref=person_id)
    with tenant_transaction():
        rows = coverage.list_coverage([person_id], include_ended=bool(request.GET.get("ended")))
    return JsonResponse({"coverage": [_coverage_json(c) for c in rows]})


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
    for item in entries:
        place[item["service"]["id"]] = place.get(item["service"]["id"], 0) + 1
        item["place"] = place[item["service"]["id"]]
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
    return JsonResponse({"referrals": _referrals_out(list(seen.values()), auth)})


@endpoint("POST")
def referral_decide(
    request: HttpRequest, auth: trust.Authenticated, referral_id: str
) -> HttpResponse:
    _staff_only(auth)
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
