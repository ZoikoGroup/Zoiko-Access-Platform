"""Endpoints behind the staff console's Exceptions, Journeys and Integrations screens, and the
platform console's Country packs screen.

Every endpoint asks the policy engine first, in the signed-in person's own organization, and every
list and action is limited to that organization: one clinic never sees or changes another clinic's
operations, journeys or API clients. Nothing here sends a patient's name or details: only
references, states and codes.
"""
from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.connector import api as connector
from modules.developer import api as developer
from modules.evidence import api as evidence
from modules.packs import api as packs
from modules.trust import api as trust
from modules.workflow import api as workflow

from .support import endpoint, is_allowed, json_body, text
from .views import _authorize, _platform_only, _staff_only

ATTENTION_STATES = ("unknown", "dead")
WAITING_STATES = ("pending", "sent", "accepted")
RECENT_LIMIT = 20
DELIVERIES_SHOWN = 5


def _org(auth: trust.Authenticated) -> str:
    """The organization this person acts in. Staff-console accounts only."""
    org = _staff_only(auth)
    if not org:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    return org


def _who(ref: str, labels: dict[str, str]) -> str:
    return "System" if ref == "system" else labels.get(ref, "Someone")


# --------------------------------------------------------------------------- exceptions


@endpoint("GET")
def staff_exceptions(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """What needs a person: partner operations whose outcome is unknown or that gave up, and
    journey deadlines that ran out. Plus what is in flight and what just finished."""
    org = _org(auth)
    _authorize(auth, "read:workflow", organization_public_id=org)
    can_act = is_allowed(auth, "write:workflow", organization_public_id=org)
    with tenant_transaction():
        operations = connector.list_operations(organization_ref=org, limit=200)
        journeys = workflow.list_exceptions(organization_ref=org)
    return JsonResponse({
        "can_act": can_act,
        "connector": {
            "attention": [o for o in operations if o["state"] in ATTENTION_STATES],
            "waiting": [o for o in operations if o["state"] in WAITING_STATES],
            "recent": [o for o in operations
                       if o["state"] in ("confirmed", "rejected")][:RECENT_LIMIT],
        },
        "journeys": journeys,
    })


@endpoint("GET")
def staff_connector_history(
    request: HttpRequest, auth: trust.Authenticated, operation_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "read:workflow", organization_public_id=org)
    with tenant_transaction():
        history = connector.operation_history(operation_id, org)
        labels = trust.actor_labels()
    for event in history["events"]:
        event["by"] = _who(event.pop("actor_ref"), labels)
    return JsonResponse({"history": history})


@endpoint("POST")
def staff_connector_redrive(
    request: HttpRequest, auth: trust.Authenticated, operation_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "write:workflow", organization_public_id=org)
    with tenant_transaction():
        done = connector.redrive(
            operation_ref=operation_id, actor_ref=auth.actor_public_id, organization_ref=org)
    return JsonResponse({"operation": done})


@endpoint("POST")
def staff_connector_resolve(
    request: HttpRequest, auth: trust.Authenticated, operation_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "write:workflow", organization_public_id=org)
    outcome = text(json_body(request), "outcome")
    with tenant_transaction():
        done = connector.resolve_by_hand(
            operation_ref=operation_id, outcome=outcome, actor_ref=auth.actor_public_id,
            organization_ref=org)
    return JsonResponse({"operation": done})


@endpoint("POST")
def staff_journey_exception_resolve(
    request: HttpRequest, auth: trust.Authenticated, exception_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "write:workflow", organization_public_id=org)
    with tenant_transaction():
        workflow.resolve_exception(
            exception_id, actor_ref=auth.actor_public_id, organization_ref=org)
    return JsonResponse({"resolved": exception_id})


# --------------------------------------------------------------------------- journeys


@endpoint("GET")
def staff_journeys(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The journey definitions in force (read only) and this clinic's journeys."""
    org = _org(auth)
    _authorize(auth, "read:workflow", organization_public_id=org)
    with tenant_transaction():
        instances = workflow.list_instances(organization_ref=org)
    by_state: dict[str, int] = {}
    for item in instances:
        key = f"{item['definition_key']}:{item['state']}"
        by_state[key] = by_state.get(key, 0) + 1
    return JsonResponse({
        "definitions": workflow.list_definitions(),
        "instances": instances,
        "by_state": by_state,
    })


@endpoint("GET")
def staff_journey_history(
    request: HttpRequest, auth: trust.Authenticated, instance_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "read:workflow", organization_public_id=org)
    with tenant_transaction():
        history = workflow.instance_history(instance_id, org)
        labels = trust.actor_labels()
    for step in history["steps"]:
        step["by"] = _who(step.pop("actor_ref"), labels)
    return JsonResponse({"history": history})


# --------------------------------------------------------------------------- integrations


@endpoint("GET")
def staff_developer(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """API clients of this clinic with their webhooks and the latest deliveries of each."""
    org = _org(auth)
    _authorize(auth, "read:partner", organization_public_id=org)
    can_manage = is_allowed(auth, "write:partner", organization_public_id=org)
    with tenant_transaction():
        clients = developer.list_clients(org)
        for client in clients:
            hooks = developer.list_webhooks(client["public_id"])
            for hook in hooks:
                hook["deliveries"] = developer.list_deliveries(
                    client["public_id"], hook["public_id"], DELIVERIES_SHOWN)
            client["webhooks"] = hooks
    return JsonResponse({
        "can_manage": can_manage, "clients": clients, "scopes": list(developer.SCOPES),
        "event_families": list(developer.PUBLIC_EVENT_PREFIXES),
    })


@endpoint("POST")
def staff_developer_client_create(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """Make an API client. Its key is in this answer and nowhere else, ever: only a hash is kept."""
    org = _org(auth)
    _authorize(auth, "write:partner", organization_public_id=org)
    body = json_body(request)
    scopes = body.get("scopes")
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["scopes"])
    with tenant_transaction():
        tenant = trust.tenant_public_id(auth.context.tenant_id)
    client, key = developer.create_client(
        tenant_public_id=tenant, name=text(body, "name"), environment=text(body, "environment"),
        scopes=scopes, created_by_ref=auth.actor_public_id, organization_ref=org)
    response = JsonResponse({"client": client, "key": key}, status=201)
    response["Cache-Control"] = "no-store"
    return response


@endpoint("POST")
def staff_developer_client_revoke(
    request: HttpRequest, auth: trust.Authenticated, client_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "write:partner", organization_public_id=org)
    with tenant_transaction():
        done = developer.revoke_client(
            client_id, actor_ref=auth.actor_public_id, organization_ref=org)
    return JsonResponse({"client": done})


@endpoint("POST")
def staff_developer_webhook_test(
    request: HttpRequest, auth: trust.Authenticated, client_id: str, webhook_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "write:partner", organization_public_id=org)
    with tenant_transaction():
        developer.client_in_organization(client_id, org)
        made = developer.send_test_event(client_id, webhook_id)
    return JsonResponse({"delivery": made}, status=202)


@endpoint("POST")
def staff_developer_delivery_redrive(
    request: HttpRequest, auth: trust.Authenticated, client_id: str, delivery_id: str
) -> HttpResponse:
    org = _org(auth)
    _authorize(auth, "write:partner", organization_public_id=org)
    with tenant_transaction():
        developer.client_in_organization(client_id, org)
        done = developer.redrive_delivery(client_id, delivery_id)
    return JsonResponse({"delivery": done}, status=202)


# --------------------------------------------------------------------------- platform: packs


@endpoint("GET")
def platform_packs(request: HttpRequest, auth: trust.Authenticated) -> HttpResponse:
    """The country packs installed in this cell, which are in force right now, and what the cell
    trusts (key ids only). Read only: packs are installed by a command, never from a screen."""
    org = _platform_only(auth)
    _authorize(auth, "read:platform", organization_public_id=org)
    with tenant_transaction():
        evidence.record(
            actor_ref=auth.actor_public_id, action="platform.packs", object_type="platform",
            object_ref="packs", outcome=evidence.AuditOutcome.ALLOWED)
    status: dict[str, Any] = packs.cell_status()
    return JsonResponse({"packs": packs.list_packs(), **status})
