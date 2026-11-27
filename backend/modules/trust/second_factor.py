"""Two-step sign-in (DEV-356): after the password (or an outside provider), a six-digit code from an
authenticator app on the person's own phone (TOTP, RFC 6238), or one of their recovery codes.

Safety rules in this file
- The shared secret is stored encrypted and shown only while the person is setting it up.
- A code is accepted once: a later sign-in needs a later code.
- Recovery codes are stored only as hashes, work once each, and are shown only when made.
- A wrong code counts toward the same lock as a wrong password, and the password step does not
  reset that count while a code is still owed, so codes cannot be guessed by signing in again.
- Between the two steps the browser holds a short-lived signed "challenge". It is not a session:
  nothing else accepts it.
- Turning it off needs the password and a code. A clinic administrator can reset it for their own
  staff who have lost both their phone and their recovery codes.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import secrets
import urllib.parse
from typing import Any

from django.conf import settings
from django.db import transaction

from foundation.cell_context import build_context, require_context, tenant_transaction
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import Clock, utc_now
from modules.evidence.api import AuditOutcome, record
from modules.policy import api as policy

from .models import Credential, SecondFactor, Status, Tenant

STEP_SECONDS = 30
DIGITS = 6
DRIFT = 1                         # one step either side, for a phone whose clock is a little off
RECOVERY_CODES = 10
CHALLENGE_LIFETIME = dt.timedelta(minutes=5)
CHALLENGE_PREFIX = "zc1"  # noqa: S105 - a label, not a secret


def _scope() -> dict[str, Any]:
    ctx = require_context()
    return {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}


def _bad(field: str) -> ZoikoError:
    return ZoikoError(ErrorCode.VALIDATION_FAILED, fields=[field])


def _audit(actor: str, action: str, outcome: AuditOutcome = AuditOutcome.ALLOWED,
           reason: str = "") -> None:
    record(actor_ref=actor, action=f"auth.second_factor_{action}", object_type="actor",
           object_ref=actor, outcome=outcome, reason_code=reason)


# ------------------------------------------------------------------ the codes
def code_at(secret: str, step: int) -> str:
    """The six digits an authenticator shows during time step `step` (RFC 6238, SHA-1)."""
    key = base64.b32decode(secret)
    digest = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def step_of(now: dt.datetime) -> int:
    return int(now.timestamp()) // STEP_SECONDS


def _matching_step(secret: str, code: str, now: dt.datetime, after: int) -> int | None:
    here = step_of(now)
    for step in range(here - DRIFT, here + DRIFT + 1):
        if step > after and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def _recovery_hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").lower().encode()).hexdigest()


def _new_recovery_codes() -> list[str]:
    raw = (secrets.token_hex(5) for _ in range(RECOVERY_CODES))
    return [f"{c[:5]}-{c[5:]}" for c in raw]


def _secret_of(row: SecondFactor) -> str:
    try:
        return decrypt(row.secret_enc, tenant_id=require_context().tenant_id,
                       context=f"secondfactor:{row.public_id}:secret")
    except CryptoError:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED) from None


def _use_code(row: SecondFactor, code: Any, now: dt.datetime) -> str:
    """Spend a code on this factor: "totp" or "recovery", or "" if it is not good."""
    if not isinstance(code, str) or len(code) > 32:
        return ""
    text = code.strip()
    if text.isdigit() and len(text) == DIGITS:
        step = _matching_step(_secret_of(row), text, now, row.last_used_step)
        if step is None:
            return ""
        row.last_used_step = step
        row.save(update_fields=["last_used_step", "updated_at"])
        return "totp"
    wanted = _recovery_hash(text)
    left = [h for h in row.recovery_hashes if not hmac.compare_digest(h, wanted)]
    if len(left) == len(row.recovery_hashes):
        return ""
    row.recovery_hashes = left
    row.save(update_fields=["recovery_hashes", "updated_at"])
    return "recovery"


def _confirmed(actor_pk: Any, *, lock: bool = False) -> SecondFactor | None:
    rows = SecondFactor.objects.filter(actor_id=actor_pk, confirmed_at__isnull=False, **_scope())
    return (rows.select_for_update() if lock else rows).first()


def required_for(actor_pk: Any) -> bool:
    """Does this person owe a code after their password? Call inside a tenant transaction."""
    return _confirmed(actor_pk) is not None


# ------------------------------------------------------------------ the challenge between steps
def _signing_key() -> bytes:
    return hmac.new(settings.SECRET_KEY.encode(), b"zoiko-second-factor-v1",
                    hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_challenge(*, tenant_public_id: str, actor_public_id: str, epoch: int, remember: bool,
                   now: dt.datetime) -> str:
    body = _b64(json.dumps({
        "t": tenant_public_id, "a": actor_public_id, "e": epoch, "r": remember,
        "exp": int((now + CHALLENGE_LIFETIME).timestamp()),
    }, separators=(",", ":")).encode())
    sign = _b64(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    return f"{CHALLENGE_PREFIX}.{body}.{sign}"


def _read_challenge(challenge: Any, now: dt.datetime) -> dict[str, Any]:
    try:
        prefix, body, sign = str(challenge).split(".")
        good = _b64(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
        if prefix != CHALLENGE_PREFIX or not hmac.compare_digest(good, sign):
            raise ValueError
        data = json.loads(_unb64(body))
        if int(data["exp"]) <= int(now.timestamp()):
            raise ValueError
        return dict(data)
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise ZoikoError(ErrorCode.UNAUTHENTICATED) from None


def verify(*, challenge: Any, code: Any, clock: Clock | None = None) -> Any:
    """The second step: the challenge from the first step and a code give the session. Every
    failure is the same 401, and counts toward the login's lock."""
    from .auth import LOCK_FOR, MAX_FAILURES, login_of_deceased, session_for  # no import cycle

    now = utc_now(clock)
    data = _read_challenge(challenge, now)
    tenant = Tenant.objects.filter(
        public_id=str(data.get("t")), cell_id=settings.ZOIKO_CELL_ID, status=Status.ACTIVE).first()
    if tenant is None:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)
    ctx = build_context(tenant.id)
    result = None
    with tenant_transaction(ctx):
        cred = (Credential.objects.select_for_update().select_related("actor")
                .filter(actor__public_id=str(data.get("a")), **_scope()).first())
        if cred is not None and cred.session_epoch == data.get("e"):
            actor = cred.actor
            locked = cred.locked_until is not None and cred.locked_until > now
            factor = _confirmed(actor.pk, lock=True)
            usable = (not locked and actor.status == Status.ACTIVE and factor is not None
                      and not login_of_deceased(actor))
            used = _use_code(factor, code, now) if usable and factor is not None else ""
            if used:
                cred.failed_attempts, cred.locked_until = 0, None
                cred.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
                _audit(actor.public_id, "passed", reason=used)
                result = session_for(tenant, cred, remember=bool(data.get("r")), now=now)
            else:
                if usable:
                    cred.failed_attempts += 1
                    if cred.failed_attempts >= MAX_FAILURES:
                        cred.locked_until, cred.failed_attempts = now + LOCK_FOR, 0
                    cred.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
                _audit(actor.public_id, "failed", AuditOutcome.DENIED,
                       "locked" if locked else "bad_code")
    if result is None:
        raise ZoikoError(ErrorCode.UNAUTHENTICATED)    # after the commit, so the count is kept
    return result


# ------------------------------------------------------------------ the person's own settings
def _own_credential(actor_public_id: str) -> Credential:
    cred = (Credential.objects.select_for_update().select_related("actor")
            .filter(actor__public_id=actor_public_id, **_scope()).first())
    if cred is None:
        raise ZoikoError(ErrorCode.NOT_FOUND)
    return cred


def _password_ok(cred: Credential, password: Any) -> None:
    from .auth import verify_password

    if not isinstance(password, str) or not verify_password(cred.password_hash, password):
        raise _bad("password")            # a 422, so the app does not treat it as signed out


def status(actor_public_id: str) -> dict[str, Any]:
    with transaction.atomic():
        row = SecondFactor.objects.filter(
            actor__public_id=actor_public_id, confirmed_at__isnull=False, **_scope()).first()
        return {"enabled": row is not None,
                "recovery_left": len(row.recovery_hashes) if row else 0}


def start_setup(actor_public_id: str, *, password: Any) -> dict[str, str]:
    """Make a new secret for the person to put in their authenticator. Nothing changes about
    signing in until they prove it works with `confirm`."""
    with transaction.atomic():
        cred = _own_credential(actor_public_id)
        _password_ok(cred, password)
        if _confirmed(cred.actor.pk) is not None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)     # already on: turn it off first
        SecondFactor.objects.filter(actor=cred.actor, **_scope()).delete()
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        row = SecondFactor(actor=cred.actor, secret_enc="", **_scope())
        row.secret_enc = encrypt(secret, tenant_id=require_context().tenant_id,
                                 context=f"secondfactor:{row.public_id}:secret")
        row.save()
        _audit(actor_public_id, "setup_started")
        label = urllib.parse.quote(f"Zoiko Access:{cred.login}")
        url = f"otpauth://totp/{label}?secret={secret}&issuer=Zoiko%20Access"
        return {"secret": secret, "otpauth_url": url}


def confirm(actor_public_id: str, *, code: Any, clock: Clock | None = None) -> list[str]:
    """The person types a code from their authenticator: two-step sign-in is now on. Returns the
    recovery codes, which are never shown again."""
    now = utc_now(clock)
    with transaction.atomic():
        row = SecondFactor.objects.select_for_update().filter(
            actor__public_id=actor_public_id, confirmed_at__isnull=True, **_scope()).first()
        if row is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        text = code.strip() if isinstance(code, str) else ""
        step = (_matching_step(_secret_of(row), text, now, row.last_used_step)
                if text.isdigit() and len(text) == DIGITS else None)
        if step is None:
            raise _bad("code")
        codes = _new_recovery_codes()
        row.confirmed_at, row.last_used_step = now, step
        row.recovery_hashes = [_recovery_hash(c) for c in codes]
        row.save(update_fields=["confirmed_at", "last_used_step", "recovery_hashes", "updated_at"])
        _audit(actor_public_id, "enabled")
        return codes


def disable(actor_public_id: str, *, password: Any, code: Any, clock: Clock | None = None) -> bool:
    """Turn it off. Returns False (and changes nothing) for a code that is not good; a wrong code
    counts toward the lock, so the caller raises after the transaction has committed."""
    from .auth import LOCK_FOR, MAX_FAILURES

    now = utc_now(clock)
    with transaction.atomic():
        cred = _own_credential(actor_public_id)
        _password_ok(cred, password)
        row = _confirmed(cred.actor.pk, lock=True)
        if row is None:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)
        locked = cred.locked_until is not None and cred.locked_until > now
        if locked or not _use_code(row, code, now):
            if not locked:
                cred.failed_attempts += 1
                if cred.failed_attempts >= MAX_FAILURES:
                    cred.locked_until, cred.failed_attempts = now + LOCK_FOR, 0
                cred.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
            _audit(actor_public_id, "failed", AuditOutcome.DENIED, "disable")
            return False
        row.delete()
        _audit(actor_public_id, "disabled")
        return True


def reset_for_member(
    member_public_id: str, *, organization_public_id: str, actor_public_id: str,
    decision: policy.Decision,
) -> None:
    """A clinic administrator turns it off for a member of their own team who has lost their phone
    and recovery codes, and signs that member out everywhere. Never for themselves."""
    from .staff_admin import _member_at, _org, _receipt

    _receipt(decision, actor_public_id, organization_public_id)
    if member_public_id == actor_public_id:
        raise ZoikoError(ErrorCode.POLICY_DENIED)
    with transaction.atomic():
        member = _member_at(member_public_id, _org(organization_public_id))
        cred = Credential.objects.select_for_update().filter(actor=member, **_scope()).first()
        removed = SecondFactor.objects.filter(actor=member, **_scope()).delete()[0]
        if cred is None or not removed:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)       # nothing to reset
        cred.session_epoch += 1
        cred.save(update_fields=["session_epoch", "updated_at"])
        record(actor_ref=actor_public_id, action="auth.second_factor_reset", object_type="actor",
               object_ref=member.public_id, outcome=AuditOutcome.ALLOWED)


def for_person(person_public_id: str) -> dict[str, bool]:
    """Whether this patient has a login and whether two-step sign-in is on for it (DEV-367)."""
    from .models import Person

    person = Person.objects.filter(public_id=person_public_id, **_scope()).first()
    if person is None or person.linked_actor_id is None:
        return {"has_login": False, "on": False}
    return {"has_login": True, "on": _confirmed(person.linked_actor_id) is not None}


def reset_for_patient(
    person_public_id: str, *, organization_ref: str, actor_public_id: str,
    clock: Clock | None = None,
) -> None:
    """Clinic staff turn two-step sign-in off for a patient who lost their phone and recovery
    codes (DEV-367), and sign that login out everywhere. Only while the patient's identity is
    verified (checked against an official document, still valid): that check is how the clinic
    knows it is really them. The caller checks the patient is connected to the clinic."""
    from .identity_proof import status_of
    from .models import Person

    with transaction.atomic():
        person = Person.objects.select_related("linked_actor").filter(
            public_id=person_public_id, **_scope()).first()
        if person is None:
            raise ZoikoError(ErrorCode.NOT_FOUND)
        if person.linked_actor is None or person.linked_actor.public_id == actor_public_id:
            raise ZoikoError(ErrorCode.POLICY_DENIED)
        if status_of(person_public_id, clock=clock).get("status") != "verified":
            raise ZoikoError(ErrorCode.POLICY_DENIED, fields=["identity"])
        cred = Credential.objects.select_for_update().filter(
            actor=person.linked_actor, **_scope()).first()
        removed = SecondFactor.objects.filter(actor=person.linked_actor, **_scope()).delete()[0]
        if cred is None or not removed:
            raise ZoikoError(ErrorCode.CONFLICT_VERSION)       # nothing to reset
        cred.session_epoch += 1
        cred.save(update_fields=["session_epoch", "updated_at"])
        record(actor_ref=actor_public_id, action="auth.second_factor_reset_patient",
               object_type="person", object_ref=person.public_id,
               outcome=AuditOutcome.ALLOWED, reason_code=organization_ref)

