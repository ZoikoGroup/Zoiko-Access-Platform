"""The public partner API, version 1 (DEV-393, GHE-13; ported from the maruthi branch onto this
branch's partner keys): /api/public/v1/.

Callers are API KEYS, not people (`modules/integration/public_keys.py`): `Authorization: Bearer
<key id>:<secret>`, only an active key that passed Zoiko's checks. Nothing here opens a person's
record: answers hold references and public catalogue facts only. A SANDBOX key only ever sees
synthetic data. Each key has its own per-minute limit, reported in X-RateLimit-* headers; over it,
429 with Retry-After and nothing done. Every read is audited in the customer's chain. The contract
is at /api/public/v1/openapi.json.

  GET ping           who am I: the key, its customer, environment and scopes
  GET services       published services of the key's customer (scope catalog:read; the
                     customer's country pack must switch the provider directory on, DEV-383)
  GET openapi.json   this contract (open: it describes the API and holds nothing about anyone)
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation import ratelimit
from foundation.cell_context import bind_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.problem import problem_response
from modules.catalog import api as catalog
from modules.evidence import api as evidence
from modules.integration import api as integration
from modules.jurisdiction import api as jurisdiction

from . import capability_gate

log = logging.getLogger("zoiko.public_api")

API_VERSION = "1.0.0"
View = Callable[..., HttpResponse]

# What a SANDBOX key sees instead of the customer's real catalogue: plainly made up.
SANDBOX_SERVICES = [
    {"id": "sandbox-service-1", "name": "Sandbox General Consultation", "category": "general",
     "duration_minutes": 30, "mode": "in_person"},
    {"id": "sandbox-service-2", "name": "Sandbox Blood Test", "category": "diagnostics",
     "duration_minutes": 15, "mode": "in_person"},
]

OPENAPI: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Zoiko Access Public API", "version": API_VERSION,
             "description": "Partner API. A key identifies a partner system with scopes; it never "
                            "opens a person's record. Sandbox keys only see synthetic data."},
    "servers": [{"url": "/api/public/v1"}],
    "components": {
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer",
                                       "description": "<key id>:<secret>"}},
        "schemas": {"Problem": {"type": "object", "properties": {
            "code": {"type": "string"}, "title": {"type": "string"},
            "status": {"type": "integer"}, "request_id": {"type": "string"},
            "retryable": {"type": "boolean"}}}},
    },
    "security": [{"bearer": []}],
    "paths": {
        "/ping": {"get": {"summary": "Who am I: the key, its customer, environment and scopes"}},
        "/services": {"get": {"summary": "Published services (scope catalog:read)"}},
    },
    "x-rate-limit": {
        "scope": "per key and clock minute (the key's own rate, 60 a minute by default)",
        "headers": ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
        "over_limit": "429 RATE_LIMITED with a Retry-After header (seconds); nothing was done",
    },
    "x-sending-events": "Partners send events to POST /api/v1/partner-events, signed with the "
                        "same key (scope events:send).",
}


def _headers(response: HttpResponse, quota: ratelimit.Quota) -> HttpResponse:
    response["X-RateLimit-Limit"] = str(quota.limit)
    response["X-RateLimit-Remaining"] = str(quota.remaining)
    response["X-RateLimit-Reset"] = str(quota.reset_seconds)
    response["Cache-Control"] = "no-store"
    return response


def key_endpoint(scope: str | None = None) -> Callable[[View], View]:
    """GET only; the key checked, counted against its own limit, its scope checked, and its
    customer bound for the view."""

    def wrap(view: View) -> View:
        @wraps(view)
        def run(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if request.method != "GET":
                response = JsonResponse({"code": "METHOD_NOT_ALLOWED"}, status=405)
                response["Allow"] = "GET"
                return response
            caller = integration.authenticate_api_key(request.headers.get("Authorization"))
            quota = ratelimit.count_against("api_key", caller.client_ref, caller.rate_per_minute)
            if not quota.allowed:                    # every request counts, allowed or not
                limited = problem_response(ErrorCode.RATE_LIMITED)
                limited["Retry-After"] = str(quota.reset_seconds)
                return _headers(limited, quota)
            if scope is not None:
                integration.require_api_scope(caller, scope)
            with bind_context(caller.context):
                return _headers(view(request, caller, *args, **kwargs), quota)

        return run

    return wrap


def _audit(caller: integration.ApiCaller, action: str) -> None:
    with tenant_transaction():
        evidence.record(actor_ref=caller.client_ref, action=action, object_type="api_key",
                        object_ref=caller.client_ref, outcome=evidence.AuditOutcome.ALLOWED,
                        reason_code=caller.environment)


@key_endpoint()
def ping(request: HttpRequest, caller: integration.ApiCaller) -> HttpResponse:
    return JsonResponse({"key": caller.client_ref, "label": caller.label,
                         "customer": {"id": caller.customer_ref, "name": caller.customer_name},
                         "environment": caller.environment, "scopes": list(caller.scopes),
                         "api_version": API_VERSION})


@key_endpoint(scope="catalog:read")
def services(request: HttpRequest, caller: integration.ApiCaller) -> HttpResponse:
    if caller.environment == "sandbox":
        _audit(caller, "public_api.services_read")
        return JsonResponse({"services": SANDBOX_SERVICES, "environment": "sandbox"})
    # The customer's market must switch the provider directory on (DEV-383), as on the app's API.
    mode = capability_gate.mode()
    if mode != capability_gate.OFF and capability_gate.DIRECTORY not in \
            jurisdiction.customer_capabilities(caller.context.tenant_id):
        if mode == capability_gate.ENFORCE:
            raise ZoikoError(ErrorCode.JURISDICTION_INACTIVE, fields=[capability_gate.DIRECTORY])
        log.warning("pack_gating would_refuse capability=%s route=public/services",
                    capability_gate.DIRECTORY)
    with tenant_transaction():
        rows = catalog.list_services(active_only=True)
    _audit(caller, "public_api.services_read")
    return JsonResponse({"environment": "live", "services": [
        {"id": s["public_id"], "name": s["name"], "category": s["category"],
         "duration_minutes": s["duration_minutes"], "mode": s["mode"]} for s in rows]})


def openapi(request: HttpRequest) -> HttpResponse:
    if request.method != "GET":
        return JsonResponse({"code": "METHOD_NOT_ALLOWED"}, status=405)
    return JsonResponse(OPENAPI)
