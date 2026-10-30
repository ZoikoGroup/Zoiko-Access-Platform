"""Email verification and password reset, using one-time links sent by email.

Rules
- A link carries "<tenant id>.<secret>". Only a hash of the secret is stored. A link works once,
  expires (48 hours to verify, 1 hour to reset), and a newer link cancels the older one.
- Asking for a reset NEVER reveals whether an account exists: the answer is always the same.
  A reset email is only sent to an address the person has verified.
- Resetting a password signs the person out everywhere and clears any lockout.
- A bad, used or expired link gives one generic error. Tokens and passwords never reach the
  audit log or the application log.
- Rate limits: a verification email at most once a minute and 5 a day per person; at most 3 reset
  emails an hour per person.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Any

from django.conf import settings

from foundation.cell_context import build_context, require_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.mail import mask_address, queue_email
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .auth import check_password_rules, set_password
from .models import Actor, ActorType, AuthToken, Credential, Status, Tenant, TokenPurpose

VERIFY_TTL = dt.timedelta(hours=48)
RESET_TTL = dt.timedelta(hours=1)
INVITE_TTL = dt.timedelta(days=7)
RESEND_MIN_GAP = dt.timedelta(seconds=60)
VERIFY_PER_DAY = 5
RESET_PER_HOUR = 3


def _bad_link() -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["token"])


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _audit(actor: str, action: str) -> None:
    record(actor_ref=actor, action=action, object_type="actor", object_ref=actor,
           outcome=AuditOutcome.ALLOWED)


def _issue(actor: Actor, purpose: str, ttl: dt.timedelta, now: dt.datetime) -> str:
    """Create a new link for this person and cancel their older unused ones of the same kind."""
    ctx = require_context()
    AuthToken.objects.filter(actor=actor, purpose=purpose, used_at__isnull=True).update(used_at=now)
    secret = secrets.token_urlsafe(32)
    AuthToken.objects.create(
        actor=actor, purpose=purpose, token_hash=_hash(secret), expires_at=now + ttl)
    tenant = Tenant.objects.get(id=ctx.tenant_id)
    return f"{tenant.public_id}.{secret}"


def _open(token: str, purpose: str, now: dt.datetime) -> tuple[AuthToken, Any]:
    """Find a live token inside ITS tenant (already bound by the caller). Raises a generic error."""
    secret = token.partition(".")[2]
    ctx = require_context()
    row = (
        AuthToken.objects.select_for_update().select_related("actor")
        .filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,     # explicit, as well as RLS
                purpose=purpose, token_hash=_hash(secret), used_at__isnull=True,
                expires_at__gt=now).first()
        if secret else None
    )
    if row is None:
        raise _bad_link()
    return row, Credential.objects.filter(actor=row.actor).first()


def _tenant_for(token: str) -> Tenant:
    tenant_public_id = token.partition(".")[0]
    tenant = Tenant.objects.filter(
        public_id=tenant_public_id, cell_id=settings.ZOIKO_CELL_ID, status=Status.ACTIVE).first()
    if tenant is None or "." not in token:
        raise _bad_link()
    return tenant


# ------------------------------------------------------------------ verification
def send_verification(actor_public_id: str, *, clock: Clock | None = None) -> bool:
    """Email a verification link to the person's address. Needs a bound tenant and a transaction.
    Returns False if the address is already verified."""
    now = utc_now(clock)
    ctx = require_context()
    actor = Actor.objects.filter(
        public_id=actor_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    cred = Credential.objects.filter(actor=actor).first() if actor else None
    if actor is None or cred is None or not cred.email:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["email"])
    if cred.email_verified_at is not None:
        return False
    sent = AuthToken.objects.filter(actor=actor, purpose=TokenPurpose.VERIFY_EMAIL)
    last = sent.order_by("-created_at").values_list("created_at", flat=True).first()
    if (last and now - last < RESEND_MIN_GAP
            or sent.filter(created_at__gte=now - dt.timedelta(days=1)).count() >= VERIFY_PER_DAY):
        raise ZoikoError(ErrorCode.RATE_LIMITED)
    token = _issue(actor, TokenPurpose.VERIFY_EMAIL, VERIFY_TTL, now)
    link = f"{settings.PATIENT_APP_URL}/?action=verify&token={token}"
    queue_email(
        kind="verify_email", to_address=cred.email, subject="Verify your email for Zoiko Access",
        body=(
            "Hello,\n\nPlease confirm this email address for your Zoiko Access account by opening "
            f"this link (it works once and expires in 48 hours):\n\n{link}\n\n"
            "If you did not create an account, you can ignore this email.\n\nZoiko Access\n"))
    _audit(actor.public_id, "auth.verification_sent")
    return True


def verify_email(token: str, *, clock: Clock | None = None) -> None:
    """Confirm an address from its link. One generic error for anything wrong with the link."""
    now = utc_now(clock)
    tenant = _tenant_for(token)
    with tenant_transaction(build_context(tenant.id)):
        row, cred = _open(token, TokenPurpose.VERIFY_EMAIL, now)
        if cred is None:
            raise _bad_link()
        row.used_at = now
        row.save(update_fields=["used_at", "updated_at"])
        cred.email_verified_at = now
        cred.save(update_fields=["email_verified_at", "updated_at"])
        _audit(row.actor.public_id, "auth.email_verified")


def email_status(actor_public_id: str) -> dict[str, Any]:
    """For the app: a masked address and whether it is verified. Needs a bound tenant."""
    ctx = require_context()
    cred = Credential.objects.filter(
        actor__public_id=actor_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    if cred is None or not cred.email:
        return {"email": "", "verified": False}
    return {"email": mask_address(cred.email), "verified": cred.email_verified_at is not None}


# ------------------------------------------------------------------ password reset
def request_password_reset(*, tenant_name: str, login: str, clock: Clock | None = None) -> None:
    """Send a reset link if (and only if) the account exists, is active, and has a verified
    address. Always returns normally, so the answer never reveals which case it was."""
    now = utc_now(clock)
    tenant = Tenant.objects.filter(
        cell_id=settings.ZOIKO_CELL_ID, name__iexact=tenant_name.strip(), status=Status.ACTIVE
    ).first() if tenant_name.strip() else None
    if tenant is None:
        return
    with tenant_transaction(build_context(tenant.id)):
        ctx = require_context()
        cred = Credential.objects.select_related("actor").filter(
            cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, login=login.strip().lower()).first()
        if (cred is None or not cred.email or cred.email_verified_at is None
                or cred.actor.status != Status.ACTIVE):
            return
        recent = AuthToken.objects.filter(
            actor=cred.actor, purpose=TokenPurpose.RESET_PASSWORD,
            created_at__gte=now - dt.timedelta(hours=1)).count()
        if recent >= RESET_PER_HOUR:
            return
        token = _issue(cred.actor, TokenPurpose.RESET_PASSWORD, RESET_TTL, now)
        link = f"{settings.PATIENT_APP_URL}/?action=reset&token={token}"
        queue_email(
            kind="reset_password", to_address=cred.email,
            subject="Reset your Zoiko Access password",
            body=(
                "Hello,\n\nSomeone asked to reset the password for your Zoiko Access account. "
                "To choose a new one, open this link (it works once and expires in 1 hour):"
                f"\n\n{link}\n\nIf this was not you, ignore this email: your password "
                "has not changed.\n\nZoiko Access\n"))
        _audit(cred.actor.public_id, "auth.reset_requested")


def send_invitation(
    actor_public_id: str, *, organization_name: str, role_label: str, clock: Clock | None = None
) -> None:
    """Email a new staff member a link to choose their own password (valid 7 days). Needs a bound
    tenant and a transaction. Limited to once a minute and 5 a day per person."""
    now = utc_now(clock)
    ctx = require_context()
    actor = Actor.objects.filter(
        public_id=actor_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
    cred = Credential.objects.filter(actor=actor).first() if actor else None
    if actor is None or cred is None or not cred.email:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["email"])
    sent = AuthToken.objects.filter(actor=actor, purpose=TokenPurpose.RESET_PASSWORD)
    last = sent.order_by("-created_at").values_list("created_at", flat=True).first()
    if (last and now - last < RESEND_MIN_GAP
            or sent.filter(created_at__gte=now - dt.timedelta(days=1)).count() >= VERIFY_PER_DAY):
        raise ZoikoError(ErrorCode.RATE_LIMITED)
    token = _issue(actor, TokenPurpose.RESET_PASSWORD, INVITE_TTL, now)
    # A doctor belongs in the doctor app, everyone else in the staff console.
    app_url = (settings.CLINICIAN_APP_URL if actor.actor_type == ActorType.CLINICIAN
               else settings.STAFF_APP_URL)
    link = f"{app_url}/?action=reset&token={token}"
    # Sign-in asks for the ORGANIZATION (the customer), not the site named in the invitation.
    tenant_name = Tenant.objects.get(id=ctx.tenant_id).name
    queue_email(
        kind="invite_staff", to_address=cred.email,
        subject=f"You have been invited to {organization_name} on Zoiko Access",
        body=(
            f"Hello,\n\nYou have been added to {organization_name} on Zoiko Access as "
            f"{role_label}. To choose your password and sign in, open this link (it works "
            f"once and expires in 7 days):\n\n{link}\n\n"
            f"To sign in, use organization {tenant_name!r} and login {cred.email}."
            "\n\nIf you were not expecting this, ignore this email.\n\nZoiko Access\n"))
    _audit(actor.public_id, "auth.invitation_sent")


def reset_password(token: str, new_password: str, *, clock: Clock | None = None) -> None:
    """Choose a new password from a reset link. The link is only used up if the password is
    accepted. Signs the person out everywhere."""
    now = utc_now(clock)
    tenant = _tenant_for(token)
    with tenant_transaction(build_context(tenant.id)):
        row, cred = _open(token, TokenPurpose.RESET_PASSWORD, now)
        if cred is None or row.actor.status != Status.ACTIVE:
            raise _bad_link()
        check_password_rules(new_password, cred.login)       # refused BEFORE the link is used up
        set_password(row.actor.public_id, login=cred.login, password=new_password,
                     by=row.actor.public_id, clock=clock)
        AuthToken.objects.filter(
            actor=row.actor, purpose=TokenPurpose.RESET_PASSWORD, used_at__isnull=True
        ).update(used_at=now)
        # The link only ever went to this person's address, so using it proves they own it.
        Credential.objects.filter(actor=row.actor, email__gt="", email_verified_at__isnull=True
                                  ).update(email_verified_at=now)
        row.used_at = now
        row.save(update_fields=["used_at", "updated_at"])
        _audit(row.actor.public_id, "auth.password_reset")
