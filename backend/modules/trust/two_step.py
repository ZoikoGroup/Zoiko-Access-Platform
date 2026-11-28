"""Two-step sign-in with an authenticator app (DEV-380, GHE-02).

After the password, the person types the 6-digit code their authenticator app shows (any app that
follows RFC 6238: 30-second codes, SHA-1, 6 digits). Ten one-time recovery codes are given when it
is switched on, for a lost phone.

Safety rules in this file
- The shared secret is stored ENCRYPTED, bound to this login (field encryption, DEV-122). It is
  shown once, while setting up, and never again.
- A code is accepted for the 30 seconds before and after its own (clock drift) and only ONCE:
  `totp_last_step` refuses any code at or before the last one used, so a code seen over a shoulder
  cannot be replayed.
- Recovery codes are kept only as keyed hashes (HMAC under a key derived from SECRET_KEY, so a
  copy of the database alone cannot test guesses), and each works once.
- Every wrong code counts towards the same lockout as a wrong password (5, then 15 minutes), and the
  counter is reset only by a right code, never by the password alone.
- Switching it on or off ends every other session, and emails a notice to a verified address.
- A customer may require it for all its staff (`Tenant.staff_two_step_required`). A staff login
  without it set up then gets a session that can only set it up (api/support.py). The person
  turning the requirement on must have it on themselves, and while it is required, staff cannot
  switch theirs off.
- Risky actions re-ask for a code when the last one was proved more than RECHECK_AFTER ago
  (`require_recent_second_step`, used by the API). Logins without two-step are not affected.
Codes, secrets and recovery codes are never logged or put in audit details.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets
import struct
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.db import transaction

from foundation.cell_context import build_context, require_context, tenant_transaction
from foundation.crypto import decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.mail import queue_email
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record

from .auth import (
    LOCK_FOR,
    MAX_FAILURES,
    REMEMBER_LIFETIME,
    TOKEN_LIFETIME,
    LoginResult,
    check_current_password,
    is_staff_login,
    issue_token,
    login_of_deceased,
    must_set_up_two_step,
    read_second_step_ticket,
)
from .models import Actor, ActorType, Credential, Organization, RoleAssignment, Status, Tenant
from .services import active_roles, organization_names

ISSUER = "Zoiko Access"
STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1                  # one step either side: up to 30 seconds of clock difference
RECOVERY_CODES = 10
# How long a proved second step counts for a risky action before the code is asked again. A
# proposal (DEV-380), like the 8-hour session it sits inside.
RECHECK_AFTER = dt.timedelta(minutes=15)

_CODE = re.compile(r"^\d{6}$")
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O or 1/I to misread


# ------------------------------------------------------------------ the codes
def _hotp(secret: bytes, counter: int) -> str:
    """RFC 4226: the code for one counter value."""
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def _step(now: dt.datetime) -> int:
    return int(now.timestamp()) // STEP_SECONDS


def code_for(secret_b32: str, now: dt.datetime) -> str:
    """The code an authenticator app shows at `now` (tests and set-up checks use it)."""
    return _hotp(base64.b32decode(secret_b32), _step(now))


def _matching_step(secret_b32: str, code: str, now: dt.datetime, after: int) -> int | None:
    """The step this code belongs to, if it is right, near now and later than `after`."""
    secret = base64.b32decode(secret_b32)
    found = None
    for step in range(_step(now) - DRIFT_STEPS, _step(now) + DRIFT_STEPS + 1):
        if step > after and hmac.compare_digest(_hotp(secret, step), code):
            found = step
    return found


def _recovery_key() -> bytes:
    return hmac.new(settings.SECRET_KEY.encode(), b"zoiko-recovery-code-v1",
                    hashlib.sha256).digest()


def _normalise_recovery(code: str) -> str:
    return re.sub(r"[\s-]", "", code).upper()


def _recovery_hash(cred: Credential, code: str) -> str:
    message = f"{cred.tenant_id}|{cred.actor.public_id}|{_normalise_recovery(code)}"
    return hmac.new(_recovery_key(), message.encode(), hashlib.sha256).hexdigest()


def _new_recovery_codes(cred: Credential) -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    cred.recovery_code_hashes = [_recovery_hash(cred, c) for c in codes]
    return codes


def _context(cred: Credential, field: str) -> str:
    return f"credential:{cred.actor.public_id}:{field}"


def _secret_of(cred: Credential) -> str:
    return decrypt(cred.totp_secret_enc, tenant_id=cred.tenant_id, context=_context(cred, "totp"))


APP, RECOVERY = "app", "recovery_code"


def _code_is_right(cred: Credential, code: object, now: dt.datetime) -> str:
    """APP or RECOVERY when it is an authenticator code or an unused recovery code, else "".
    Uses it up (the caller saves `cred`). A spent recovery code is reported by email."""
    if not isinstance(code, str) or not code.strip() or cred.totp_enabled_at is None:
        return ""
    typed = code.strip().replace(" ", "")
    if _CODE.match(typed):
        step = _matching_step(_secret_of(cred), typed, now, cred.totp_last_step)
        if step is None:
            return ""
        cred.totp_last_step = step
        return APP
    if len(_normalise_recovery(typed)) != 10:
        return ""
    wanted = _recovery_hash(cred, typed)
    left = [h for h in cred.recovery_code_hashes if not hmac.compare_digest(h, wanted)]
    if len(left) == len(cred.recovery_code_hashes):
        return ""
    cred.recovery_code_hashes = left
    if cred.email and cred.email_verified_at is not None:
        queue_email(kind="recovery_code_used", to_address=cred.email,
                    values={"left": str(len(left))})
    return RECOVERY


def _count_wrong(cred: Credential, now: dt.datetime) -> None:
    cred.failed_attempts += 1
    if cred.failed_attempts >= MAX_FAILURES:
        cred.locked_until, cred.failed_attempts = now + LOCK_FOR, 0


def _locked(cred: Credential, now: dt.datetime) -> bool:
    return cred.locked_until is not None and cred.locked_until > now


def _audit(actor: str, action: str, outcome: AuditOutcome, reason: str = "") -> None:
    record(actor_ref=actor, action=action, object_type="actor", object_ref=actor,
           outcome=outcome, reason_code=reason)


def _notify(cred: Credential, switched_on: bool) -> None:
    if not (cred.email and cred.email_verified_at is not None):
        return
    what = "switched on" if switched_on else "switched OFF"
    queue_email(kind="two_step_changed", to_address=cred.email, values={"what": what})


def _own_credential(actor_public_id: str) -> Credential:
    ctx = require_context()
    cred = Credential.objects.select_for_update().select_related("actor").filter(
        cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, actor__public_id=actor_public_id).first()
    if cred is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)     # a development login without a password
    return cred


def _tenant() -> Tenant:
    return Tenant.objects.get(id=require_context().tenant_id)


def _fresh_token(cred: Credential, now: dt.datetime, second_step_at: int) -> str:
    return issue_token(tenant_public_id=_tenant().public_id, actor_public_id=cred.actor.public_id,
                       epoch=cred.session_epoch, now=now, second_step_at=second_step_at)


# ------------------------------------------------------------------ what a person sees
def status(actor_public_id: str) -> dict[str, object]:
    """Whether it is on, how many recovery codes are left, and whether this customer requires it."""
    ctx = require_context()
    cred = Credential.objects.select_related("actor").filter(
        cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, actor__public_id=actor_public_id).first()
    tenant = _tenant()
    staff = cred is not None and is_staff_login(cred.actor.actor_type)
    return {
        "available": cred is not None,           # a password login (not a development link)
        "on": cred is not None and cred.totp_enabled_at is not None,
        "on_since": cred.totp_enabled_at.isoformat() if cred and cred.totp_enabled_at else None,
        "recovery_codes_left": len(cred.recovery_code_hashes) if cred else 0,
        "setting_up": cred is not None and bool(cred.totp_pending_enc),
        "required": bool(staff and tenant.staff_two_step_required),
        "recheck_minutes": int(RECHECK_AFTER.total_seconds() // 60),
    }


# ------------------------------------------------------------------ switching it on
@dataclass(frozen=True)
class SetUp:
    """`secret` is shown ONCE, as text and as an otpauth:// link (what a QR code would hold)."""
    ok: bool
    secret: str = ""
    link: str = ""


def start_set_up(actor_public_id: str, *, password: object, clock: Clock | None = None) -> SetUp:
    """Step 1: the CURRENT password is needed (a stolen session alone is not enough). Gives a new
    secret, kept aside until its first code is entered. ok=False for a wrong password, which the
    caller must COMMIT, so the counter is kept."""
    now = utc_now(clock)
    with transaction.atomic():
        cred = _own_credential(actor_public_id)
        if cred.totp_enabled_at is not None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["two_step"])   # already on
        if not check_current_password(cred, password, now, "auth.two_step_set_up"):
            return SetUp(ok=False)
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        cred.totp_pending_enc = encrypt(
            secret, tenant_id=cred.tenant_id, context=_context(cred, "totp_pending"))
        cred.save(update_fields=["totp_pending_enc", "updated_at"])
        _audit(actor_public_id, "auth.two_step_set_up", AuditOutcome.ALLOWED, "started")
    label = quote(f"{ISSUER}:{cred.login}")
    link = f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits=6&period=30"
    return SetUp(ok=True, secret=secret, link=link)


@dataclass(frozen=True)
class Switched:
    """`recovery_codes` are shown ONCE. `token` replaces this session's (all others ended)."""
    ok: bool
    token: str = ""
    recovery_codes: tuple[str, ...] = ()


def finish_set_up(actor_public_id: str, *, code: object, clock: Clock | None = None) -> Switched:
    """Step 2: the first code from the app proves it was added correctly. Then it is on."""
    now = utc_now(clock)
    with transaction.atomic():
        cred = _own_credential(actor_public_id)
        if cred.totp_enabled_at is not None or not cred.totp_pending_enc:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["two_step"])
        if _locked(cred, now):
            _audit(actor_public_id, "auth.two_step_set_up", AuditOutcome.DENIED, "locked")
            return Switched(ok=False)
        secret = decrypt(cred.totp_pending_enc, tenant_id=cred.tenant_id,
                         context=_context(cred, "totp_pending"))
        typed = code.strip() if isinstance(code, str) else ""
        step = _matching_step(secret, typed, now, 0) if _CODE.match(typed) else None
        if step is None:
            _count_wrong(cred, now)
            cred.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
            _audit(actor_public_id, "auth.two_step_set_up", AuditOutcome.DENIED, "bad_code")
            return Switched(ok=False)
        cred.totp_secret_enc = encrypt(secret, tenant_id=cred.tenant_id,
                                       context=_context(cred, "totp"))
        cred.totp_pending_enc, cred.totp_enabled_at, cred.totp_last_step = "", now, step
        codes = _new_recovery_codes(cred)
        cred.failed_attempts, cred.locked_until = 0, None
        cred.session_epoch += 1                                # every other device signs in again
        cred.save()
        _audit(actor_public_id, "auth.two_step_on", AuditOutcome.ALLOWED)
        _notify(cred, switched_on=True)
        return Switched(ok=True, token=_fresh_token(cred, now, int(now.timestamp())),
                        recovery_codes=tuple(codes))


def switch_off(
    actor_public_id: str, *, password: object, code: object, clock: Clock | None = None,
) -> Switched:
    """Needs the password AND a code. Refused while the customer requires it for staff."""
    now = utc_now(clock)
    with transaction.atomic():
        cred = _own_credential(actor_public_id)
        if cred.totp_enabled_at is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["two_step"])   # already off
        if _tenant().staff_two_step_required and is_staff_login(cred.actor.actor_type):
            raise ZoikoError(ErrorCode.POLICY_DENIED, fields=["required"])
        if not check_current_password(cred, password, now, "auth.two_step_off"):
            return Switched(ok=False)
        if not _code_is_right(cred, code, now):
            _count_wrong(cred, now)
            cred.save(update_fields=["failed_attempts", "locked_until", "totp_last_step",
                                     "recovery_code_hashes", "updated_at"])
            _audit(actor_public_id, "auth.two_step_off", AuditOutcome.DENIED, "bad_code")
            return Switched(ok=False)
        cred.totp_secret_enc = cred.totp_pending_enc = ""
        cred.totp_enabled_at, cred.totp_last_step, cred.recovery_code_hashes = None, 0, []
        cred.failed_attempts, cred.locked_until = 0, None
        cred.session_epoch += 1
        cred.save()
        _audit(actor_public_id, "auth.two_step_off", AuditOutcome.ALLOWED)
        _notify(cred, switched_on=False)
        return Switched(ok=True, token=_fresh_token(cred, now, 0))


def new_recovery_codes(actor_public_id: str, *, code: object,
                       clock: Clock | None = None) -> Switched:
    """A fresh set of ten (the old ones stop working). Needs a code."""
    now = utc_now(clock)
    with transaction.atomic():
        cred = _own_credential(actor_public_id)
        if cred.totp_enabled_at is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["two_step"])
        if _locked(cred, now) or not _code_is_right(cred, code, now):
            if not _locked(cred, now):
                _count_wrong(cred, now)
            cred.save(update_fields=["failed_attempts", "locked_until", "totp_last_step",
                                     "recovery_code_hashes", "updated_at"])
            _audit(actor_public_id, "auth.recovery_codes", AuditOutcome.DENIED, "bad_code")
            return Switched(ok=False)
        codes = _new_recovery_codes(cred)
        cred.failed_attempts, cred.locked_until = 0, None
        cred.save()
        _audit(actor_public_id, "auth.recovery_codes", AuditOutcome.ALLOWED)
        return Switched(ok=True, recovery_codes=tuple(codes))


# ------------------------------------------------------------------ signing in, and re-checks
def finish_sign_in(*, ticket: object, code: object, clock: Clock | None = None) -> LoginResult:
    """The second step of sign-in: the ticket from the right password, and a code. Every failure
    is the same UNAUTHENTICATED, raised after the wrong-attempt counter is committed."""
    now = utc_now(clock)
    proof = read_second_step_ticket(ticket, now=now)
    tenant = Tenant.objects.filter(
        public_id=proof.tenant, cell_id=settings.ZOIKO_CELL_ID, status=Status.ACTIVE).first()
    if tenant is None:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    ctx = build_context(tenant.id)
    result: LoginResult | None = None
    with tenant_transaction(ctx):
        cred = Credential.objects.select_for_update().select_related("actor").filter(
            cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, actor__public_id=proof.actor).first()
        if cred is None:
            raise ZoikoError(ErrorCode.UNAUTHENTICATED)
        actor = cred.actor
        usable = (cred.session_epoch == proof.epoch and actor.status == Status.ACTIVE
                  and not login_of_deceased(actor) and cred.totp_enabled_at is not None)
        if not usable or _locked(cred, now):
            _audit(actor.public_id, "auth.login_second_step", AuditOutcome.DENIED,
                   "locked" if usable else "inactive")
        elif kind := _code_is_right(cred, code, now):
            cred.failed_attempts, cred.locked_until, cred.last_login_at = 0, None, now
            cred.save()
            names = organization_names()
            roles = [(r, o, names.get(o, "")) for r, o in active_roles(actor.public_id, at=now)]
            lifetime = REMEMBER_LIFETIME if proof.remember else TOKEN_LIFETIME
            token = issue_token(
                tenant_public_id=tenant.public_id, actor_public_id=actor.public_id,
                epoch=cred.session_epoch, now=now, lifetime=lifetime,
                second_step_at=int(now.timestamp()))
            _audit(actor.public_id, "auth.login_second_step", AuditOutcome.ALLOWED, kind)
            result = LoginResult(
                token, now + lifetime, actor.public_id, actor.actor_type, cred.login,
                tenant.public_id, tenant.name, tenant.type, roles, remembered=proof.remember,
                must_set_up_two_step=must_set_up_two_step(tenant, actor, cred))
        else:
            _count_wrong(cred, now)
            cred.save(update_fields=["failed_attempts", "locked_until", "totp_last_step",
                                     "recovery_code_hashes", "updated_at"])
            _audit(actor.public_id, "auth.login_second_step", AuditOutcome.DENIED, "bad_code")
    if result is None:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)     # AFTER the commit: the counter is kept
    return result


def recheck(actor_public_id: str, *, code: object, clock: Clock | None = None) -> str | None:
    """A code typed again before a risky action. A fresh token that counts as just proved, or
    None for a wrong code (the caller commits, so the counter is kept)."""
    now = utc_now(clock)
    with transaction.atomic():
        cred = _own_credential(actor_public_id)
        if cred.totp_enabled_at is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["two_step"])
        if _locked(cred, now) or not _code_is_right(cred, code, now):
            if not _locked(cred, now):
                _count_wrong(cred, now)
            cred.save(update_fields=["failed_attempts", "locked_until", "totp_last_step",
                                     "recovery_code_hashes", "updated_at"])
            _audit(actor_public_id, "auth.two_step_recheck", AuditOutcome.DENIED, "bad_code")
            return None
        cred.failed_attempts, cred.locked_until = 0, None
        cred.save()
        _audit(actor_public_id, "auth.two_step_recheck", AuditOutcome.ALLOWED)
        return _fresh_token(cred, now, int(now.timestamp()))


def needs_recheck(*, two_step_on: bool, second_step_at: int, clock: Clock | None = None) -> bool:
    """Must the code be typed again before a risky action? Only for logins that have it on."""
    if not two_step_on:
        return False
    return utc_now(clock).timestamp() - second_step_at > RECHECK_AFTER.total_seconds()


# ------------------------------------------------------------------ the customer's rule
def set_staff_requirement(enabled: object, *, by: str, by_has_two_step: bool) -> bool:
    """The customer's administrator requires two-step sign-in for every staff login (or stops).
    To turn it on they must have it on themselves (they would otherwise be the first one stuck)."""
    if not isinstance(enabled, bool):
        raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["required"])
    if enabled and not by_has_two_step:
        raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["two_step"])
    ctx = require_context()
    with transaction.atomic():
        Tenant.objects.filter(id=ctx.tenant_id, cell_id=ctx.cell_id).update(
            staff_two_step_required=enabled)
        record(actor_ref=by, action="tenant.staff_two_step", object_type="tenant",
               object_ref=str(ctx.tenant_id)[:36], outcome=AuditOutcome.ALLOWED,
               reason_code="required" if enabled else "not_required")
    return enabled


def staff_two_step_required() -> bool:
    """Whether the bound customer (or Zoiko's platform tenant) requires two-step for all staff."""
    return bool(_tenant().staff_two_step_required)


def staff_without_two_step() -> int:
    """How many active staff password logins have not set it up (for the administrator)."""
    ctx = require_context()
    rows = Credential.objects.select_related("actor").filter(
        cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, totp_enabled_at__isnull=True,
        actor__status=Status.ACTIVE)
    return sum(1 for c in rows if is_staff_login(c.actor.actor_type))


def admin_reset(
    member_public_id: str,
    *,
    by: str,
    organization_public_id: str,
    decision: Any,
) -> dict[str, Any]:
    """An administrator resets a staff member's lost two-step sign-in (DEV-380). Ends every
    session of that login, audits the reset, and emails a notice if verified. Not yourself."""
    from modules.policy import api as policy

    policy.check_receipt(
        decision, actor_public_id=by, operation="write:staff",
        organization_public_id=organization_public_id, subject_ref=None)
    if member_public_id == by:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    ctx = require_context()
    with transaction.atomic():
        org = Organization.objects.filter(
            public_id=organization_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
            status=Status.ACTIVE).first()
        if org is None:
            raise ZoikoError(ErrorCode.VALIDATION_FAILED, fields=["organization"])
        actor = Actor.objects.filter(
            public_id=member_public_id, cell_id=ctx.cell_id, tenant_id=ctx.tenant_id
        ).exclude(actor_type__in=(ActorType.PATIENT, ActorType.PROXY)).first()
        if actor is None or not RoleAssignment.objects.filter(
                actor=actor, organization=org, cell_id=ctx.cell_id,
                tenant_id=ctx.tenant_id).exists():
            raise ZoikoError(ErrorCode.NOT_FOUND)
        cred = Credential.objects.select_for_update().filter(
            cell_id=ctx.cell_id, tenant_id=ctx.tenant_id, actor=actor).first()
        if cred is None or cred.totp_enabled_at is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION, fields=["two_step"])
        cred.totp_secret_enc = cred.totp_pending_enc = ""
        cred.totp_enabled_at, cred.totp_last_step, cred.recovery_code_hashes = None, 0, []
        cred.failed_attempts, cred.locked_until = 0, None
        cred.session_epoch += 1
        cred.save()
        record(actor_ref=by, action="auth.two_step_admin_reset", object_type="actor",
               object_ref=actor.public_id, outcome=AuditOutcome.ALLOWED)
        if cred.email and cred.email_verified_at is not None:
            queue_email(kind="two_step_changed", to_address=cred.email,
                        values={"what": "reset by an administrator"})
        return {"id": actor.public_id, "two_step_on": False}
