"""Password login, signed session tokens, and sign-out. A development-grade stand-in for the
real identity provider (DEV-030): the rest of the system only sees `authenticate()` results.

Safety rules in this file
- Passwords are stored only as salted scrypt hashes and compared in constant time.
- EVERY login failure (unknown organization, unknown login, wrong password, locked, suspended)
  gives the same answer, and unknown logins cost the same time as known ones.
- Five wrong passwords lock the login for 15 minutes. Failure counters are committed even
  though the request then fails.
- Tokens are HMAC-signed, expire after 8 hours, and carry a session epoch: raising the epoch
  (sign-out, password change) kills every token already issued.
- Tokens and passwords are never logged or stored in audit details.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import IntegrityError, transaction

from foundation.cell_context import build_context, require_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .models import Actor, ActorType, Credential, Organization, Status, Tenant
from .people import create_person, link_person_to_actor
from .services import active_roles, assign_role, organization_names, register_actor

if TYPE_CHECKING:
    from .dev_auth import Authenticated

TOKEN_PREFIX = "zt1"  # noqa: S105 - a label, not a secret
TOKEN_LIFETIME = dt.timedelta(hours=8)
MAX_FAILURES = 5
LOCK_FOR = dt.timedelta(minutes=15)
MAX_PASSWORD = 128

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1

REGISTER_WINDOW = dt.timedelta(minutes=10)
REGISTER_MAX = 20            # new accounts per organization per window: a flood brake
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


# ------------------------------------------------------------------ password hashing
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(stored: str, password: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
            dklen=len(expected))
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


# A real-looking hash to spend the same time on when the login does not exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_password_rules(password: str, login: str) -> None:
    """Minimal rules: long enough (PASSWORD_MIN_LENGTH), not the login, not one repeated char."""
    minimum = getattr(settings, "PASSWORD_MIN_LENGTH", 10)
    if not isinstance(password, str) or not minimum <= len(password) <= MAX_PASSWORD:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["password"])
    if password.lower() == login.lower() or len(set(password)) < 4:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["password"])


# ------------------------------------------------------------------ tokens
def _signing_key() -> bytes:
    secret = settings.SECRET_KEY.encode()
    return hmac.new(secret, b"zoiko-session-token-v1", hashlib.sha256).digest()


def _sign(body: str) -> str:
    return _b64(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())


def issue_token(
    *, tenant_public_id: str, actor_public_id: str, epoch: int, now: dt.datetime
) -> str:
    payload = {
        "t": tenant_public_id, "a": actor_public_id, "e": epoch,
        "iat": int(now.timestamp()), "exp": int((now + TOKEN_LIFETIME).timestamp()),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{TOKEN_PREFIX}.{body}.{_sign(body)}"


@dataclass(frozen=True)
class TokenClaims:
    tenant: str
    actor: str
    epoch: int


def read_token(token: str, *, now: dt.datetime | None = None) -> TokenClaims:
    """Check the signature and expiry. Raises UNAUTHENTICATED for anything wrong."""
    try:
        prefix, body, signature = token.split(".")
        if prefix != TOKEN_PREFIX or not hmac.compare_digest(_sign(body), signature):
            raise ValueError
        data = json.loads(_unb64(body))
        if int(data["exp"]) <= int((now or utc_now()).timestamp()):
            raise ValueError
        return TokenClaims(str(data["t"]), str(data["a"]), int(data["e"]))
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise ZoikoError(ErrorCode.UNAUTHENTICATED) from None


# ------------------------------------------------------------------ results
@dataclass(frozen=True)
class LoginResult:
    token: str
    expires_at: dt.datetime
    actor_public_id: str
    actor_type: str
    login: str
    tenant_public_id: str
    tenant_name: str
    tenant_type: str
    roles: list[tuple[str, str, str]]   # (role code, organization id, organization name)


def _fail() -> ZoikoError:
    return ZoikoError(ErrorCode.UNAUTHENTICATED)


def _audit(actor: str, action: str, outcome: AuditOutcome, reason: str = "") -> None:
    record(actor_ref=actor, action=action, object_type="actor", object_ref=actor,
           outcome=outcome, reason_code=reason)


# ------------------------------------------------------------------ login and sign-out
def login(
    *, tenant_name: str, login: str, password: str, clock: Clock | None = None
) -> LoginResult:
    """Sign in with organization name, login and password. One answer for every failure."""
    now = utc_now(clock)
    login_id = login.strip().lower()
    tenant = (
        Tenant.objects.filter(
            cell_id=settings.ZOIKO_CELL_ID, name__iexact=tenant_name.strip(), status=Status.ACTIVE)
        .first() if tenant_name.strip() else None
    )
    if tenant is None or not login_id or not isinstance(password, str) or not password:
        verify_password(_DUMMY_HASH, password if isinstance(password, str) else "")
        raise _fail()

    ctx = build_context(tenant.id)
    result: LoginResult | None = None
    with tenant_transaction(ctx):
        cred = (
            Credential.objects.select_for_update().select_related("actor")
            .filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, login=login_id).first()
        )
        if cred is None:
            verify_password(_DUMMY_HASH, password)
        else:
            locked = cred.locked_until is not None and cred.locked_until > now
            password_ok = verify_password(cred.password_hash, password)
            actor = cred.actor
            if password_ok and not locked and actor.status == Status.ACTIVE:
                cred.failed_attempts, cred.locked_until, cred.last_login_at = 0, None, now
                cred.save(update_fields=["failed_attempts", "locked_until", "last_login_at",
                                         "updated_at"])
                names = organization_names()
                roles = [(r, o, names.get(o, "")) for r, o in active_roles(actor.public_id, at=now)]
                token = issue_token(
                    tenant_public_id=tenant.public_id, actor_public_id=actor.public_id,
                    epoch=cred.session_epoch, now=now)
                _audit(actor.public_id, "auth.login", AuditOutcome.ALLOWED)
                result = LoginResult(
                    token, now + TOKEN_LIFETIME, actor.public_id, actor.actor_type, login_id,
                    tenant.public_id, tenant.name, tenant.type, roles)
            else:
                reason = "locked" if locked else (
                    "inactive" if password_ok else "bad_password")
                if not locked:
                    cred.failed_attempts += 1
                    if cred.failed_attempts >= MAX_FAILURES:
                        cred.locked_until, cred.failed_attempts = now + LOCK_FOR, 0
                        reason = "locked_now"
                    cred.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
                _audit(actor.public_id, "auth.login", AuditOutcome.DENIED, reason)
    if result is None:
        raise _fail()      # raised AFTER the transaction commits, so the counters are kept
    return result


_sign_in = login


def register_patient(
    *, tenant_name: str, login: str, password: str, clock: Clock | None = None
) -> LoginResult:
    """A patient creates their own account, if the organization allows it. Signs them in.

    Never takes over an existing account. The login must look like an email address. NOTE: the
    email is NOT verified (no email sending yet), so it proves nothing about who owns it.
    """
    now = utc_now(clock)
    login_id = login.strip().lower()
    if len(login_id) > 128 or not _EMAIL.match(login_id):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["login"])
    tenant = (
        Tenant.objects.filter(
            cell_id=settings.ZOIKO_CELL_ID, name__iexact=tenant_name.strip(), status=Status.ACTIVE)
        .first() if tenant_name.strip() else None
    )
    if tenant is None or not tenant.self_registration:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    ctx = build_context(tenant.id)
    with tenant_transaction(ctx):
        scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
        recent = Credential.objects.filter(created_at__gte=now - REGISTER_WINDOW, **scope).count()
        if recent >= REGISTER_MAX:
            raise ZoikoError(ErrorCode.RATE_LIMITED)
        orgs = Organization.objects.filter(status=Status.ACTIVE, **scope)
        org = orgs.order_by("created_at").first()
        if org is None:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if (
            Actor.objects.filter(issuer="local", subject=login_id, **scope).exists()
            or Credential.objects.filter(login=login_id, **scope).exists()
        ):
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)         # never take over an account
        actor = register_actor(
            actor_type=ActorType.PATIENT, issuer="local", subject=login_id, by="self")
        person = create_person(by=actor.public_id)
        link_person_to_actor(person.public_id, actor.public_id, by=actor.public_id)
        assign_role(
            actor_public_id=actor.public_id, organization_public_id=org.public_id,
            role_code="R01", by=actor.public_id, clock=clock)
        set_password(actor.public_id, login=login_id, password=password, by=actor.public_id,
                     email=login_id, clock=clock)
        record(actor_ref=actor.public_id, action="auth.register", object_type="actor",
               object_ref=actor.public_id, outcome=AuditOutcome.ALLOWED)
        from .email_flows import send_verification  # noqa: PLC0415 - avoids an import cycle

        send_verification(actor.public_id, clock=clock)
    return _sign_in(tenant_name=tenant.name, login=login_id, password=password, clock=clock)


def authenticate_token(token: str, *, clock: Clock | None = None) -> Authenticated:
    """Turn a signed token into an Authenticated result, re-checking the database each time."""
    from .dev_auth import Authenticated

    claims = read_token(token, now=utc_now(clock))
    tenant = Tenant.objects.filter(
        public_id=claims.tenant, cell_id=settings.ZOIKO_CELL_ID, status=Status.ACTIVE).first()
    if tenant is None:
        raise _fail()
    ctx = build_context(tenant.id)
    with tenant_transaction(ctx):
        cred = (
            Credential.objects.select_related("actor")
            .filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                    actor__public_id=claims.actor).first()
        )
        roles = (
            tuple(r for r, _ in active_roles(cred.actor.public_id))
            if cred is not None and cred.actor.status == Status.ACTIVE else ()
        )
    if cred is None or cred.actor.status != Status.ACTIVE or cred.session_epoch != claims.epoch:
        raise _fail()
    return Authenticated(
        ctx, cred.actor.public_id, cred.actor.actor_type, tenant.name, tenant.type, roles)


def revoke_sessions(actor_public_id: str, *, by: str) -> None:
    """Sign this actor out everywhere: every token already issued stops working."""
    ctx = require_context()
    with transaction.atomic():
        cred = Credential.objects.select_for_update().filter(
            cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, actor__public_id=actor_public_id
        ).first()
        if cred is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        cred.session_epoch += 1
        cred.save(update_fields=["session_epoch", "updated_at"])
        record(actor_ref=by, action="auth.sign_out", object_type="actor",
               object_ref=actor_public_id, outcome=AuditOutcome.ALLOWED)


def set_password(
    actor_public_id: str,
    *,
    login: str,
    password: str,
    by: str,
    email: str | None = None,
    clock: Clock | None = None,
) -> None:
    """Create or change an actor's password login. Changing it signs them out everywhere.
    `email` (optional) sets where their mail goes; a changed address is unverified again."""
    login_id = login.strip().lower()
    if not login_id or len(login_id) > 128:
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["login"])
    check_password_rules(password, login_id)
    ctx = require_context()
    now = utc_now(clock)
    with transaction.atomic():
        actor = Actor.objects.filter(
            public_id=actor_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id).first()
        if actor is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        cred = Credential.objects.select_for_update().filter(actor=actor).first()
        try:
            with transaction.atomic():
                new_email = email.strip().lower() if email else None
                if cred is None:
                    Credential.objects.create(
                        actor=actor, login=login_id, password_hash=hash_password(password),
                        password_changed_at=now, email=new_email or "")
                else:
                    cred.login, cred.password_hash = login_id, hash_password(password)
                    cred.failed_attempts, cred.locked_until = 0, None
                    cred.session_epoch += 1
                    cred.password_changed_at = now
                    if new_email is not None and new_email != cred.email:
                        cred.email, cred.email_verified_at = new_email, None
                    cred.save()
        except IntegrityError:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION) from None   # login already taken
        record(actor_ref=by, action="auth.set_password", object_type="actor",
               object_ref=actor_public_id, outcome=AuditOutcome.ALLOWED)
