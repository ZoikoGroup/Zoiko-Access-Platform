"""An organization's administrator manages their own team: invite, suspend, re-invite.

Rules
- Every change needs a policy receipt for `write:staff` at the administrator's organization
  (obtained from policy.authorize, which records refusals).
- Only these roles can be handed out here: doctor, receptionist, administrator. Zoiko's own roles
  (support, safety, integration) and the patient and partner roles can never be granted this way.
- A new person gets NO password from us: they receive an invitation email with a one-time link to
  choose their own. Until then the account has a random password nobody knows.
- An administrator cannot suspend themselves, and can only touch people who hold a role at their
  own organization. Patients and guardians are not "team members" and cannot be reached here.
"""
from __future__ import annotations

import secrets
from typing import Any

from django.db import transaction

from foundation.cell_context import require_context
from foundation.errors import ErrorCode, ZoikoError
from foundation.mail import clean_address
from foundation.timeutil import Clock
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .auth import set_password
from .email_flows import send_invitation
from .models import Actor, ActorType, Credential, Organization, RoleAssignment, Status
from .services import assign_role, register_actor, set_actor_status

WRITE_STAFF = "write:staff"

# role code -> (what kind of login it is, what we call it in the invitation)
INVITABLE_ROLES: dict[str, tuple[ActorType, str]] = {
    "R03": (ActorType.CLINICIAN, "a doctor"),
    "R04": (ActorType.FACILITY_USER, "a receptionist"),
    "R13": (ActorType.FACILITY_USER, "an administrator"),
}


def _receipt(decision: policy.Decision, actor_public_id: str, organization_public_id: str) -> None:
    policy.check_receipt(
        decision, actor_public_id=actor_public_id, operation=WRITE_STAFF,
        organization_public_id=organization_public_id, subject_ref=None,
    )


def _org(organization_public_id: str) -> Organization:
    ctx = require_context()
    org = Organization.objects.filter(
        public_id=organization_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
        status=Status.ACTIVE).first()
    if org is None:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["organization"])
    return org


def _member_at(member_public_id: str, org: Organization) -> Actor:
    """A team member = a non-patient actor holding a role at THIS organization. Else NOT_FOUND."""
    ctx = require_context()
    actor = Actor.objects.filter(
        public_id=member_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id
    ).exclude(actor_type__in=(ActorType.PATIENT, ActorType.PROXY)).first()
    if actor is None or not RoleAssignment.objects.filter(
            actor=actor, organization=org, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).exists():
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return actor


def invite_staff(
    *,
    email: str,
    role_code: str,
    organization_public_id: str,
    actor_public_id: str,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Add a person to the organization and email them an invitation."""
    _receipt(decision, actor_public_id, organization_public_id)
    if role_code not in INVITABLE_ROLES:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["role"])
    address = clean_address(email)
    ctx = require_context()
    with transaction.atomic():
        org = _org(organization_public_id)
        if (
            Actor.objects.filter(issuer="local", subject=address, cell_id=ctx.cell_id,
                                 tenant_id=ctx.tenant_id).exists()
            or Credential.objects.filter(login=address, cell_id=ctx.cell_id,
                                         tenant_id=ctx.tenant_id).exists()
        ):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)       # that login is already taken
        kind, label = INVITABLE_ROLES[role_code]
        actor = register_actor(actor_type=kind, issuer="local", subject=address,
                               by=actor_public_id)
        assign_role(actor_public_id=actor.public_id, organization_public_id=org.public_id,
                    role_code=role_code, by=actor_public_id, clock=clock)
        # A password nobody knows: the person sets their own from the invitation link.
        set_password(actor.public_id, login=address, password=secrets.token_urlsafe(24),
                     by=actor_public_id, email=address, clock=clock)
        send_invitation(actor.public_id, organization_name=org.name, role_label=label,
                        clock=clock)
        record(actor_ref=actor_public_id, action="staff.invite", object_type="actor",
               object_ref=actor.public_id, outcome=AuditOutcome.ALLOWED,
               details={"role": role_code})
        return {"id": actor.public_id, "login": address, "role": role_code}


def set_member_status(
    member_public_id: str,
    status: str,
    *,
    organization_public_id: str,
    actor_public_id: str,
    decision: policy.Decision,
) -> dict[str, Any]:
    """Suspend or restore a team member. Not yourself."""
    _receipt(decision, actor_public_id, organization_public_id)
    if status not in (Status.ACTIVE, Status.SUSPENDED):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["status"])
    if member_public_id == actor_public_id:
        raise ZoikoError(ErrorCode.POLICY_DENIED)               # nobody locks themselves out
    with transaction.atomic():
        member = _member_at(member_public_id, _org(organization_public_id))
        done = set_actor_status(member.public_id, Status(status), by=actor_public_id)
        return {"id": done.public_id, "status": done.status}


def resend_invitation(
    member_public_id: str,
    *,
    organization_public_id: str,
    actor_public_id: str,
    decision: policy.Decision,
    clock: Clock | None = None,
) -> None:
    """Send the invitation again to someone who has not accepted it yet."""
    _receipt(decision, actor_public_id, organization_public_id)
    with transaction.atomic():
        org = _org(organization_public_id)
        member = _member_at(member_public_id, org)
        cred = Credential.objects.filter(actor=member).first()
        if cred is None or not cred.email or cred.email_verified_at is not None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)    # already accepted: nothing to resend
        role = (
            RoleAssignment.objects.filter(actor=member, organization=org)
            .select_related("role").order_by("-created_at").first()
        )
        label = INVITABLE_ROLES.get(role.role.role_code if role else "", (None, "a team member"))[1]
        send_invitation(member.public_id, organization_name=org.name, role_label=label, clock=clock)
