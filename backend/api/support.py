"""Shared plumbing for the HTTP endpoints: login, tenant binding, request parsing."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from foundation.cell_context import bind_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.request_context import (
    get_stated_purpose,
    reset_audit_extra,
    set_audit_extra,
)
from modules.packs import api as packs
from modules.policy import api as policy
from modules.trust import api as trust

# Why a person is acting, decided from the kind of login. The real login will carry this.
PURPOSE_BY_ACTOR_TYPE = {  # fallback only, see purpose_for
    "patient": "SELF_SERVICE",
    "proxy": "SELF_SERVICE",
    "clinician": "DIRECT_CARE",
    "facility_user": "OPERATIONS",
    "payer_user": "PAYMENT_COVERAGE",
    "employer_user": "PROGRAM_ADMIN",
    "workload": "INTEGRATION",
    "support": "SUPPORT",
}

# Which app a person belongs in. Decided from the kind of login and the roles they hold.
OPERATIONS_ROLES = frozenset({"R04", "R10", "R11", "R12", "R13"})


PLATFORM_ROLES = frozenset({"R11", "R12"})


def home_for(actor_type: str, role_codes: list[str], tenant_type: str = "") -> str:
    """patient | platform | operations | clinician | payer | employer | partner | none

    "platform" is Zoiko's own staff: R11 or R12 in a tenant of type `platform`. The same roles in
    a customer's tenant belong to the customer's operations area instead."""
    roles = set(role_codes)
    if tenant_type == "platform" and roles & PLATFORM_ROLES:
        return "platform"
    if actor_type in ("patient", "proxy") or roles & {"R01", "R02"}:
        return "patient"
    if roles & OPERATIONS_ROLES:
        return "operations"
    if "R03" in roles:
        return "clinician"
    if "R07" in roles:
        return "payer"
    if "R08" in roles:
        return "employer"
    if roles & {"R05", "R06", "R09"}:
        return "partner"
    return "none"


View = Callable[..., HttpResponse]


def require_feature(capability: str) -> None:
    """Refuse a feature the country's pack does not switch on (GHE-04). Does nothing unless packs
    are enforced (ZOIKO_PACKS_ENFORCED). With enforcement on, no country, no pack, an expired or
    revoked pack, or a pack that does not list the capability all mean REFUSED. Reading what
    already exists is never gated: only starting something new."""
    if settings.PACKS_ENFORCED:
        packs.require_capability(settings.ZOIKO_COUNTRY, capability)


def endpoint(*methods: str) -> Callable[[View], Callable[..., HttpResponse]]:
    """Wrap a view: only these methods, only a logged-in actor, tenant bound for its duration."""

    def wrap(view: View) -> Callable[..., HttpResponse]:
        @wraps(view)
        def run(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if request.method not in methods:
                response = JsonResponse({"code": "METHOD_NOT_ALLOWED"}, status=405)
                response["Allow"] = ", ".join(methods)
                return response
            header = request.headers.get("Authorization") or ""
            auth = trust.authenticate(header or None)
            # What the audit record keeps about this person: their kind of login, which session
            # (a fingerprint of the token, never the token) and the reason they stated.
            token = set_audit_extra({
                "actor_ref": auth.actor_public_id, "actor_kind": auth.actor_type,
                "purpose_code": purpose_for(auth),
                "session_ref": hashlib.sha256(header.encode()).hexdigest()[:16],
            })
            try:
                with bind_context(auth.context):
                    return view(request, auth, *args, **kwargs)
            finally:
                reset_audit_extra(token)

        return run

    return wrap


def json_body(request: HttpRequest) -> dict[str, Any]:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED) from None
    if not isinstance(data, dict):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED)
    return data


def text(body: dict[str, Any], key: str, required: bool = True) -> str:
    value = body.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[key])
    return value.strip()


# Why a person is acting, from the roles they hold (first match wins). The kind of login is only a
# fallback for people with no role yet. A person holding several roles gets ONE purpose per
# request; they cannot act under two at once.
PURPOSE_BY_ROLE = (
    ("R12", "SAFETY_REVIEW"), ("R11", "SUPPORT"), ("R10", "INTEGRATION"), ("R13", "OPERATIONS"),
    ("R04", "OPERATIONS"),
    ("R03", "DIRECT_CARE"), ("R07", "PAYMENT_COVERAGE"), ("R08", "PROGRAM_ADMIN"),
    ("R05", "OPERATIONS"), ("R06", "OPERATIONS"), ("R09", "OPERATIONS"),
    ("R02", "SELF_SERVICE"), ("R01", "SELF_SERVICE"),
)


def purpose_for(auth: trust.Authenticated) -> str:
    """The reason this request is made. A person may state it (the doctor app sends it); the
    policy engine then refuses it unless one of their roles may act under it. With none stated,
    the default for their role is used."""
    stated = get_stated_purpose()
    if stated:
        return stated
    for role, purpose in PURPOSE_BY_ROLE:
        if role in auth.roles:
            return purpose
    return PURPOSE_BY_ACTOR_TYPE.get(auth.actor_type, "OPERATIONS")


def held_organizations(auth: trust.Authenticated) -> list[str]:
    """Organizations where this login holds an active role (needs a transaction)."""
    from foundation.cell_context import tenant_transaction

    with tenant_transaction():
        return sorted({org for _, org in trust.active_roles(auth.actor_public_id)})


def is_allowed(auth: trust.Authenticated, operation: str, **kwargs: Any) -> bool:
    """Ask the policy engine. A denial is recorded, then reported as False."""
    try:
        policy.authorize(
            actor_public_id=auth.actor_public_id, operation=operation,
            purpose_code=purpose_for(auth), announce=False, **kwargs,
        )
    except ZoikoError:
        return False
    return True
