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
from modules.evidence import api as evidence
from modules.policy import api as policy
from modules.referral import api as referral
from modules.scheduling import api as scheduling
from modules.trust import api as trust

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
    }


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
            return JsonResponse({"services": [_service_json(s, orgs) for s in rows]})
    _authorize(auth, "read:catalog", organization_public_id=_catalog_org(auth))
    with tenant_transaction():
        orgs = trust.organization_names()
        rows = catalog.list_services(query=request.GET.get("q", ""))
        out = []
        for s in rows:
            out.append(_service_json(s, orgs))
    return JsonResponse({"services": out})


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
    return JsonResponse({"bookings": rows})


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
    return JsonResponse({"tenant": auth.tenant_name, "counts": numbers,
                         **_evidence_block(auth, org)})


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
        return JsonResponse(trust.patient_registry())


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
    return JsonResponse({"appointments": out})


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
    return JsonResponse({"patients": out})


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


def _referrals_out(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with tenant_transaction():
        orgs, labels = trust.organization_names(), trust.actor_labels()
        cache: dict[str, Any] = {}
        return [_referral_json(r, cache, orgs, labels) for r in rows]


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
            [r for r in rows if r["person_ref"] in allowed])})
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
    return JsonResponse({"referral": _referrals_out([made])[0]}, status=201)


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
    return JsonResponse({"referrals": _referrals_out(list(seen.values()))})


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
    return JsonResponse({"referral": _referrals_out([done])[0]})


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
    return JsonResponse({"referrals": _referrals_out(rows)})
