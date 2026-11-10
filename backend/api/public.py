"""The public partner API, version 1 (GHE-13): /api/public/v1/.

Callers are API CLIENTS, not people: they hold a key with scopes, and nothing here opens a person's
record. Every answer is shaped for a partner (references and public catalog facts only). A sandbox
client only ever receives synthetic data. The contract is published at /api/public/v1/openapi.json.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation import idempotency
from foundation.cell_context import bind_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from modules.catalog import api as catalog
from modules.developer import api as developer

from .support import json_body, text

View = Callable[..., HttpResponse]
API_VERSION = "1.0.0"
IDEMPOTENCY_RETENTION = dt.timedelta(days=1)

# What a sandbox client sees instead of the customer's real catalog.
SANDBOX_SERVICES = [
    {"id": "sandbox-service-1", "name": "Sandbox General Consultation", "category": "general",
     "duration_minutes": 30, "mode": "in_person"},
    {"id": "sandbox-service-2", "name": "Sandbox Blood Test", "category": "diagnostics",
     "duration_minutes": 15, "mode": "in_person"},
]

OPENAPI: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Zoiko Access Public API", "version": API_VERSION,
             "description": "Partner API. Keys identify a client with scopes; they never open a "
                            "person's record. Sandbox keys only see synthetic data."},
    "servers": [{"url": "/api/public/v1"}],
    "components": {
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "schemas": {"Problem": {"type": "object", "properties": {
            "code": {"type": "string"}, "title": {"type": "string"},
            "status": {"type": "integer"}, "request_id": {"type": "string"},
            "retryable": {"type": "boolean"}}}},
    },
    "security": [{"bearer": []}],
    "paths": {
        "/ping": {"get": {"summary": "Who am I (client, environment, scopes)"}},
        "/services": {"get": {"summary": "Published services (scope catalog:read)"}},
        "/webhooks": {
            "get": {"summary": "List webhooks (scope webhooks:manage)"},
            "post": {"summary": "Create a webhook; needs an Idempotency-Key header "
                                "(16-128 characters). The signing secret is shown once."}},
        "/webhooks/{id}/pause": {"post": {"summary": "Pause a webhook"}},
        "/webhooks/{id}/resume": {"post": {"summary": "Resume a webhook"}},
        "/webhooks/{id}/test": {"post": {"summary": "Queue a synthetic ping delivery"}},
        "/webhooks/{id}/deliveries": {"get": {"summary": "Recent deliveries of a webhook"}},
        "/deliveries/{id}/redrive": {"post": {"summary": "Try a given-up delivery again"}},
    },
    "x-webhook-signing": {
        "headers": ["X-Zoiko-Event-Id", "X-Zoiko-Event-Type", "X-Zoiko-Timestamp",
                    "X-Zoiko-Signature", "Idempotency-Key"],
        "signature": "v1=HMAC-SHA256(secret, '<timestamp>.' + body), hex",
        "replay_protection": "reject timestamps more than 300 seconds from now",
        "delivery": "at least once; de-duplicate on X-Zoiko-Event-Id",
        "event_families": list(developer.PUBLIC_EVENT_PREFIXES),
    },
}


def client_endpoint(methods: tuple[str, ...], scope: str | None = None) -> Callable[[View], View]:
    """Authenticate the API key, check its scope, and bind the customer it belongs to."""

    def wrap(view: View) -> View:
        @wraps(view)
        def run(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if request.method not in methods:
                response = JsonResponse({"code": "METHOD_NOT_ALLOWED"}, status=405)
                response["Allow"] = ", ".join(methods)
                return response
            client = developer.authenticate_key(request.headers.get("Authorization"))
            if scope is not None:
                developer.require_scope(client, scope)
            with bind_context(client.context):
                return view(request, client, *args, **kwargs)

        return run

    return wrap


def openapi(request: HttpRequest) -> HttpResponse:
    """The versioned contract. Public: it describes the API and holds nothing about anyone."""
    if request.method != "GET":
        return JsonResponse({"code": "METHOD_NOT_ALLOWED"}, status=405)
    return JsonResponse(OPENAPI)


@client_endpoint(("GET",))
def ping(request: HttpRequest, client: developer.AuthenticatedClient) -> HttpResponse:
    return JsonResponse({
        "client": client.client_ref, "environment": client.environment,
        "scopes": list(client.scopes), "api_version": API_VERSION})


@client_endpoint(("GET",), "catalog:read")
def services(request: HttpRequest, client: developer.AuthenticatedClient) -> HttpResponse:
    if client.environment == "sandbox":
        return JsonResponse({"services": SANDBOX_SERVICES, "sandbox": True})
    with tenant_transaction():
        rows = catalog.list_services()
    return JsonResponse({"sandbox": False, "services": [
        {"id": s["public_id"], "name": s["name"], "category": s["category"],
         "duration_minutes": s["duration_minutes"], "mode": s["mode"]} for s in rows]})


@client_endpoint(("GET", "POST"), "webhooks:manage")
def webhooks(request: HttpRequest, client: developer.AuthenticatedClient) -> HttpResponse:
    if request.method == "GET":
        with tenant_transaction():
            return JsonResponse({"webhooks": developer.list_webhooks(client.client_ref)})
    body = json_body(request)
    url = text(body, "url")
    events = body.get("events")
    if not isinstance(events, list) or not all(isinstance(e, str) for e in events):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["events"])
    made: dict[str, Any] = {}

    def create() -> dict[str, Any]:
        hook, secret = developer.create_webhook(
            client_ref=client.client_ref, url=url, event_prefixes=events)
        made["secret"] = secret                      # shown once; never saved in the replay record
        return {"webhook": hook["public_id"]}

    with tenant_transaction():
        outcome = idempotency.run(
            scope=f"client:{client.client_ref} POST /webhooks",
            key=request.headers.get("Idempotency-Key", ""), payload=body, work=create,
            retain_for=IDEMPOTENCY_RETENTION)
        listed = [w for w in developer.list_webhooks(client.client_ref)
                  if w["public_id"] == outcome.result["webhook"]]
    response = JsonResponse(
        {"webhook": listed[0], "secret": made.get("secret")},
        status=200 if outcome.replayed else 201)
    response["Idempotent-Replayed"] = "true" if outcome.replayed else "false"
    return response


@client_endpoint(("POST",), "webhooks:manage")
def webhook_pause(
    request: HttpRequest, client: developer.AuthenticatedClient, webhook_id: str
) -> HttpResponse:
    with tenant_transaction():
        return JsonResponse({"webhook": developer.set_webhook_status(
            client.client_ref, webhook_id, "paused")})


@client_endpoint(("POST",), "webhooks:manage")
def webhook_resume(
    request: HttpRequest, client: developer.AuthenticatedClient, webhook_id: str
) -> HttpResponse:
    with tenant_transaction():
        return JsonResponse({"webhook": developer.set_webhook_status(
            client.client_ref, webhook_id, "active")})


@client_endpoint(("POST",), "webhooks:manage")
def webhook_test(
    request: HttpRequest, client: developer.AuthenticatedClient, webhook_id: str
) -> HttpResponse:
    with tenant_transaction():
        return JsonResponse(
            {"delivery": developer.send_test_event(client.client_ref, webhook_id)}, status=202)


@client_endpoint(("GET",), "webhooks:manage")
def webhook_deliveries(
    request: HttpRequest, client: developer.AuthenticatedClient, webhook_id: str
) -> HttpResponse:
    with tenant_transaction():
        return JsonResponse({"deliveries": developer.list_deliveries(
            client.client_ref, webhook_id)})


@client_endpoint(("POST",), "webhooks:manage")
def delivery_redrive(
    request: HttpRequest, client: developer.AuthenticatedClient, delivery_id: str
) -> HttpResponse:
    with tenant_transaction():
        return JsonResponse({"delivery": developer.redrive_delivery(
            client.client_ref, delivery_id)}, status=202)
