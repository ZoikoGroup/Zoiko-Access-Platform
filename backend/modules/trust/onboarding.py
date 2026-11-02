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
from .services import assign_role, create_organization, create_tenant, register_actor

WRITE_PLATFORM = "write:platform"
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
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Create the customer and invite its first administrator. Call with the platform tenant
    bound and NO transaction open (creating the customer runs its own)."""
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_PLATFORM,
        organization_public_id=platform_organization_public_id, subject_ref=None)
    customer_name = _name(customer_name, "customer")
    site_name = _name(site_name, "site")
    address = clean_address(admin_email)                    # refuses a bad address
    tenant = create_tenant(name=customer_name, type=TenantType.PROVIDER_ORG)  # 409 if taken
    with tenant_transaction(build_context(tenant.id)):
        site = create_organization(name=site_name, by=actor_public_id)
        admin = register_actor(
            actor_type=ActorType.FACILITY_USER, issuer="local", subject=address,
            by=actor_public_id)
        assign_role(actor_public_id=admin.public_id, organization_public_id=site.public_id,
                    role_code="R13", by=actor_public_id, clock=clock)
        set_password(admin.public_id, login=address, password=secrets.token_urlsafe(24),
                     by=actor_public_id, email=address, clock=clock)
        send_invitation(admin.public_id, organization_name=site.name,
                        role_label="an administrator", clock=clock)
        record(actor_ref=actor_public_id, action="customer.onboard", object_type="tenant",
               object_ref=tenant.public_id, outcome=AuditOutcome.ALLOWED)
    # The platform's own audit chain records that it happened (no address, no names).
    with tenant_transaction():
        record(actor_ref=actor_public_id, action="platform.onboard_customer",
               object_type="tenant", object_ref=tenant.public_id, outcome=AuditOutcome.ALLOWED)
    return {"id": tenant.public_id, "name": tenant.name, "site": site.name}
