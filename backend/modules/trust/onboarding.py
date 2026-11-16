"""Zoiko's own staff add a new customer: the organization, its first site and its administrator.

Rules
- Only a platform login holding a role that may WRITE the platform resource (support, R11) can
  do this. The caller passes a policy receipt, checked here against the platform organization.
- The first administrator (R13) gets NO password from us: they receive the usual invitation
  email with a one-time link to choose their own. Until then the account has a random password
  nobody knows.
- Everything that can be checked is checked BEFORE the customer is created, so a bad email or an
  empty name never leaves half a customer behind. The customer starts closed to self-sign-up.
- Zoiko staff never see the administrator's address again: it is not returned, and the platform
  audit entry carries no personal detail.
"""
from __future__ import annotations

import re
import secrets
from typing import Any

from foundation.cell_context import build_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.mail import clean_address
from foundation.timeutil import Clock
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .auth import set_password
from .email_flows import send_invitation
from .models import ActorType, TenantType
from .services import (
    assign_role,
    context_for,
    create_organization,
    create_tenant,
    list_tenants,
    register_actor,
    set_country,
    set_self_registration,
)

WRITE_PLATFORM = "write:platform"

# What kind of customer Zoiko support is onboarding: (customer type, the kind of login its first
# user has, that user's role, what the invitation calls them). TEMPORARY proposal (DEV-158): the
# specification names the roles and portals but not how a partner, payer or employer is admitted.
CUSTOMER_KINDS: dict[str, tuple[TenantType, ActorType, str, str]] = {
    "provider": (TenantType.PROVIDER_ORG, ActorType.FACILITY_USER, "R13", "an administrator"),
    "lab": (TenantType.PARTNER, ActorType.FACILITY_USER, "R05", "a lab or imaging operator"),
    "pharmacy": (TenantType.PARTNER, ActorType.FACILITY_USER, "R06", "a pharmacy operator"),
    "homecare": (TenantType.PARTNER, ActorType.FACILITY_USER, "R09", "a home-care coordinator"),
    "payer": (TenantType.PAYER, ActorType.PAYER_USER, "R07", "a payer user"),
    "employer": (TenantType.EMPLOYER, ActorType.EMPLOYER_USER, "R08", "an employer sponsor"),
    "developer": (TenantType.PARTNER, ActorType.FACILITY_USER, "R10", "a partner developer"),
}
MAX_NAME = 120


def _name(value: str, field: str) -> str:
    value = (value or "").strip()
    if not 2 <= len(value) <= MAX_NAME or any(ch in value for ch in "\r\n\t"):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])
    return value


def onboard_customer(
    *,
    customer_name: str,
    site_name: str,
    admin_email: str,
    actor_public_id: str,
    platform_organization_public_id: str,
    decision: policy.Decision,
    kind: str = "provider",
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Create the customer and invite its first user (an administrator for a clinic; for another
    kind of customer, the one role that kind of customer holds). Call with the platform tenant
    bound and NO transaction open (creating the customer runs its own)."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_PLATFORM,
        organization_public_id=platform_organization_public_id, subject_ref=None)
    if kind not in CUSTOMER_KINDS:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["kind"])
    tenant_type, actor_type, role_code, role_label = CUSTOMER_KINDS[kind]
    customer_name = _name(customer_name, "customer")
    site_name = _name(site_name, "site")
    address = clean_address(admin_email)                    # refuses a bad address
    tenant = create_tenant(name=customer_name, type=tenant_type)  # 409 if taken
    with tenant_transaction(build_context(tenant.id)):
        site = create_organization(name=site_name, by=actor_public_id)
        admin = register_actor(
            actor_type=actor_type, issuer="local", subject=address, by=actor_public_id)
        assign_role(actor_public_id=admin.public_id, organization_public_id=site.public_id,
                    role_code=role_code, by=actor_public_id, clock=clock)
        set_password(admin.public_id, login=address, password=secrets.token_urlsafe(24),
                     by=actor_public_id, email=address, clock=clock)
        send_invitation(admin.public_id, organization_name=site.name,
                        role_label=role_label, clock=clock)
        record(actor_ref=actor_public_id, action="customer.onboard", object_type="tenant",
               object_ref=tenant.public_id, outcome=AuditOutcome.ALLOWED)
    # The platform's own audit chain records that it happened (no address, no names).
    with tenant_transaction():
        record(actor_ref=actor_public_id, action="platform.onboard_customer",
               object_type="tenant", object_ref=tenant.public_id, outcome=AuditOutcome.ALLOWED)
    return {"id": tenant.public_id, "name": tenant.name, "site": site.name, "kind": kind}


def set_customer_sign_up(
    *,
    customer_public_id: str,
    enabled: object,
    actor_public_id: str,
    platform_organization_public_id: str | None,
    decision: policy.Decision,
) -> dict[str, Any]:
    """Open or close a customer's patient self-sign-up. Call with the platform tenant bound and NO
    transaction open (the change runs in the customer's own tenant).

    Rules: needs the right to WRITE the platform; `enabled` must be a real true or false; the
    platform's own tenant has no patient sign-up and is refused; setting the value it already has
    changes nothing and writes no audit entry. A real change is audited in the customer's own
    chain (who changed it, no personal detail) and in the platform's chain."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_PLATFORM,
        organization_public_id=platform_organization_public_id, subject_ref=None)
    if not isinstance(enabled, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["enabled"])
    found = next((t for t in list_tenants() if t["public_id"] == customer_public_id), None)
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if found["type"] == TenantType.PLATFORM:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["customer"])
    if (found["self_registration"] == "true") != enabled:
        with tenant_transaction(context_for(customer_public_id)):
            set_self_registration(enabled, by=actor_public_id)
        with tenant_transaction():
            record(actor_ref=actor_public_id, action="platform.customer_sign_up",
                   object_type="tenant", object_ref=customer_public_id,
                   outcome=AuditOutcome.ALLOWED, details={"enabled": int(enabled)})
    return {"id": customer_public_id, "name": found["name"], "self_registration": enabled}


_COUNTRY = re.compile(r"^[A-Z]{2}$")


def set_customer_country(
    *,
    customer_public_id: str,
    country: object,
    actor_public_id: str,
    platform_organization_public_id: str | None,
    decision: policy.Decision,
) -> dict[str, Any]:
    """Record the country a customer operates in (an ISO code, or "" to clear it). Same shape as
    the sign-up change: call with the platform tenant bound and NO transaction open. The country
    only decides which country pack's settings apply; it grants nothing (DEV-133)."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_PLATFORM,
        organization_public_id=platform_organization_public_id, subject_ref=None)
    if not isinstance(country, str) or (country and not _COUNTRY.match(country)):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["country"])
    found = next((t for t in list_tenants() if t["public_id"] == customer_public_id), None)
    if found is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    if found["type"] == TenantType.PLATFORM:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["customer"])
    if found["country"] != country:
        with tenant_transaction(context_for(customer_public_id)):
            set_country(country, by=actor_public_id)
        with tenant_transaction():
            record(actor_ref=actor_public_id, action="platform.customer_country",
                   object_type="tenant", object_ref=customer_public_id,
                   outcome=AuditOutcome.ALLOWED, details={"set": int(bool(country))})
    return {"id": customer_public_id, "name": found["name"], "country": country}
