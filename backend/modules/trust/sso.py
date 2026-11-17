"""Sign in with an outside identity provider (OpenID Connect): Google, or a company's own.

One standard implementation serves every provider that speaks OpenID Connect (Google, Microsoft
Entra, Okta, Auth0, Keycloak): the provider is configuration, not code. The flow is the
authorization-code flow with PKCE:

  1. `start` sends the person to the provider with a one-time `state`, a `nonce` and a PKCE
     challenge. What we need to finish is kept for ten minutes and can be used once.
  2. The person signs in at the provider and comes back with a `code` and the same `state`.
  3. `finish` uses the state up, exchanges the code for an ID token, and checks it: the signature
     (RS256 only, against the provider's published keys), who issued it, who it is for, that it has
     not expired, and that it carries the nonce we made. Nothing in it is trusted before that.
  4. The person is matched to a Zoiko actor: by the provider's stable subject once linked; the first
     time, only by an email address that the provider says it verified AND that the account has
     itself verified. No account is ever created from a provider's say-so, and an email alone never
     links two accounts that disagree.

Every failure is the same answer (UNAUTHENTICATED) with a reason recorded in the audit trail. This
module only authenticates: roles and what a person may do are still decided by the policy engine,
exactly as for a password sign-in.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.db import IntegrityError, transaction

from foundation.cell_context import build_context, tenant_transaction
from foundation.crypto import CryptoError, decrypt, encrypt
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import UTC, Clock, utc_now
from modules.evidence.api import AuditOutcome

from .auth import TOKEN_LIFETIME, LoginResult, _audit, issue_token
from .models import (
    Credential,
    ExternalIdentity,
    SsoAttempt,
    Status,
    Tenant,
)
from .services import active_roles, organization_names

ATTEMPT_LIFETIME = dt.timedelta(minutes=10)
CLOCK_SKEW = dt.timedelta(seconds=60)
JWKS_TTL = 600.0
MAX_BODY = 1_000_000
MIN_RSA_BITS = 2048
GOOGLE = {
    "key": "google", "name": "Google", "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
}


class SsoError(Exception):
    """Something went wrong talking to the provider or reading its answer. No detail on purpose."""


def _fail() -> ZoikoError:
    """The one answer for every failure; the real reason goes to the audit trail only."""
    return ZoikoError(ErrorCode.UNAUTHENTICATED)


# ------------------------------------------------------------------ providers
@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    issuer: str
    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    scopes: str = "openid email"


def _make_provider(raw: dict[str, Any]) -> Provider | None:
    try:
        discovered: dict[str, Any] = {}
        explicit = all(raw.get(k) for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"))
        if raw.get("issuer") and not explicit:
            discovered = _discover(str(raw["issuer"]))
        merged = {**discovered, **{k: v for k, v in raw.items() if v}}
        provider = Provider(
            key=str(merged["key"]), name=str(merged.get("name") or merged["key"]),
            issuer=str(merged["issuer"]), client_id=str(merged["client_id"]),
            client_secret=str(merged["client_secret"]),
            authorization_endpoint=str(merged["authorization_endpoint"]),
            token_endpoint=str(merged["token_endpoint"]), jwks_uri=str(merged["jwks_uri"]),
            scopes=str(merged.get("scopes") or "openid email"))
    except (KeyError, SsoError):
        return None
    if not all((provider.key, provider.client_id, provider.client_secret)):
        return None
    if not all(_https_ok(u) for u in (provider.authorization_endpoint, provider.token_endpoint,
                                      provider.jwks_uri)):
        return None
    return provider


_DISCOVERY: dict[str, tuple[float, dict[str, Any]]] = {}


def _discover(issuer: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _DISCOVERY.get(issuer)
    if cached and now - cached[0] < JWKS_TTL:
        return cached[1]
    doc = http_json(issuer.rstrip("/") + "/.well-known/openid-configuration")
    found: dict[str, Any] = {
        k: doc[k] for k in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")
        if k in doc}
    if len(found) != 4:
        raise SsoError
    _DISCOVERY[issuer] = (now, found)
    return found


def providers() -> dict[str, Provider]:
    """The providers this server offers: those fully configured, and nothing else."""
    out: dict[str, Provider] = {}
    gid = getattr(settings, "GOOGLE_CLIENT_ID", "")
    secret = getattr(settings, "GOOGLE_CLIENT_SECRET", "")
    if gid and secret:
        made = _make_provider({**GOOGLE, "client_id": gid, "client_secret": secret})
        if made:
            out[made.key] = made
    raw = getattr(settings, "SSO_PROVIDERS", "")
    if raw:
        try:
            items = json.loads(raw) if isinstance(raw, str) else list(raw)
        except ValueError:
            items = []
        for item in items:
            if isinstance(item, dict):
                made = _make_provider(item)
                if made:
                    out[made.key] = made
    return out


def public_providers() -> list[dict[str, str]]:
    """What the sign-in page may show: a key and a name, never a client id or secret."""
    return [{"key": p.key, "name": p.name} for p in providers().values()]


def _redirect_allowed(uri: str) -> bool:
    allowed = [u.strip() for u in str(getattr(settings, "SSO_REDIRECT_URIS", "")).split(",")
               if u.strip()]
    return uri in allowed


def _https_ok(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.netloc:
        return True
    insecure_ok = bool(getattr(settings, "SSO_ALLOW_INSECURE", False))
    return insecure_ok and parts.scheme in ("http", "https")


# ------------------------------------------------------------------ talking to the provider
def http_json(url: str, *, data: dict[str, str] | None = None, timeout: float = 5.0
              ) -> dict[str, Any]:
    """GET (or POST a form) and read JSON. The one place this module touches the network, so tests
    replace it. Bounded in time and size."""
    if not _https_ok(url):
        raise SsoError
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(  # noqa: S310 - the scheme is checked above
        url, data=body, method="POST" if data is not None else "GET",
        headers={"Accept": "application/json",
                 **({"Content-Type": "application/x-www-form-urlencoded"} if data is not None
                    else {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise SsoError
        parsed = json.loads(raw)
    except (urllib.error.URLError, OSError, ValueError):
        raise SsoError from None
    if not isinstance(parsed, dict):
        raise SsoError
    return parsed


_JWKS: dict[str, tuple[float, dict[str, Any]]] = {}


def _jwks(provider: Provider, *, refresh: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    cached = _JWKS.get(provider.key)
    if cached and not refresh and now - cached[0] < JWKS_TTL:
        return cached[1]
    keys = http_json(provider.jwks_uri)
    _JWKS[provider.key] = (now, keys)
    return keys


def reset_caches() -> None:
    _JWKS.clear()
    _DISCOVERY.clear()


# ------------------------------------------------------------------ the ID token
def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text.encode("ascii") + b"=" * (-len(text) % 4))
    except (ValueError, binascii.Error, UnicodeError):
        raise SsoError from None


def _public_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey:
    if jwk.get("kty") != "RSA":
        raise SsoError
    n = int.from_bytes(_b64d(str(jwk["n"])), "big")
    e = int.from_bytes(_b64d(str(jwk["e"])), "big")
    if n.bit_length() < MIN_RSA_BITS:
        raise SsoError
    return rsa.RSAPublicNumbers(e, n).public_key()


def verify_id_token(token: str, provider: Provider, *, nonce_hash: str, now: dt.datetime
                    ) -> dict[str, Any]:
    """Return the token's claims if, and only if, every check passes."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64d(head_b64))
        claims = json.loads(_b64d(body_b64))
        signature = _b64d(sig_b64)
    except (ValueError, SsoError):
        raise SsoError from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise SsoError
    if header.get("alg") != "RS256":                    # never "none", never a shared-secret alg
        raise SsoError
    kid = header.get("kid")
    verified = False
    for refresh in (False, True):                       # a rotated provider key: fetch once more
        try:
            keyset = _jwks(provider, refresh=refresh)
            candidates = [k for k in keyset.get("keys", []) if isinstance(k, dict)
                          and (kid is None or k.get("kid") == kid)]
            for jwk in candidates:
                try:
                    _public_key(jwk).verify(
                        signature, f"{head_b64}.{body_b64}".encode(), padding.PKCS1v15(),
                        hashes.SHA256())
                    verified = True
                    break
                except (InvalidSignature, SsoError, KeyError, ValueError):
                    continue
        except SsoError:
            if refresh:
                raise
        if verified:
            break
    if not verified:
        raise SsoError
    issuers = {provider.issuer}
    if provider.key == "google":
        issuers.add("accounts.google.com")
    if claims.get("iss") not in issuers:
        raise SsoError
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if provider.client_id not in audiences:
        raise SsoError
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != provider.client_id:
        raise SsoError
    try:
        exp_raw: Any = claims["exp"]
        iat_raw: Any = claims["iat"] if "iat" in claims else exp_raw
        exp = dt.datetime.fromtimestamp(float(exp_raw), UTC)
        iat = dt.datetime.fromtimestamp(float(iat_raw), UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        raise SsoError from None
    if exp + CLOCK_SKEW <= now or iat - CLOCK_SKEW > now:
        raise SsoError
    given_nonce = claims.get("nonce")
    if not isinstance(given_nonce, str) or not secrets.compare_digest(
            _hash(given_nonce), nonce_hash):
        raise SsoError
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise SsoError
    return claims


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ------------------------------------------------------------------ step 1: start
def start(*, provider_key: str, tenant_name: str, redirect_uri: str,
          clock: Clock | None = None) -> str:
    """Make the one-time state and return the provider's sign-in address."""
    provider = providers().get(provider_key)
    if provider is None or not _redirect_allowed(redirect_uri):
        raise _fail()
    now = utc_now(clock)
    tenant = (Tenant.objects.filter(cell_id=settings.ZOIKO_CELL_ID, status=Status.ACTIVE,
                                    name__iexact=tenant_name.strip()).first()
              if tenant_name.strip() else None)
    if tenant is None:
        raise _fail()
    state = f"{tenant.public_id}.{secrets.token_urlsafe(24)}"
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                         ).rstrip(b"=").decode()
    ctx = build_context(tenant.id)
    state_hash = _hash(state)
    with tenant_transaction(ctx):
        SsoAttempt.objects.filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                                  expires_at__lt=now).delete()          # tidy as we go
        SsoAttempt.objects.create(
            provider=provider.key, state_hash=state_hash, nonce_hash=_hash(nonce),
            verifier_enc=encrypt(verifier, tenant_id=ctx.tenant_id,
                                 context=f"sso:{state_hash}:verifier"),
            redirect_uri=redirect_uri, expires_at=now + ATTEMPT_LIFETIME,
            cell_id=ctx.cell_id, tenant_id=ctx.tenant_id)
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": provider.client_id, "redirect_uri": redirect_uri,
        "scope": provider.scopes, "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    return f"{provider.authorization_endpoint}?{query}"


# ------------------------------------------------------------------ step 2: finish
def finish(*, provider_key: str, code: str, state: str, redirect_uri: str,
           clock: Clock | None = None) -> LoginResult:
    """Use the state up, check the provider's answer, and sign the matching actor in."""
    provider = providers().get(provider_key)
    if provider is None or not code or not state or len(code) > 2048 or len(state) > 200:
        raise _fail()
    now = utc_now(clock)
    tenant_id = state.split(".", 1)[0]
    tenant = Tenant.objects.filter(cell_id=settings.ZOIKO_CELL_ID, status=Status.ACTIVE,
                                   public_id=tenant_id).first()
    if tenant is None:
        raise _fail()
    ctx = build_context(tenant.id)
    state_hash = _hash(state)

    # The state is used up first, in its own transaction, so that any later failure still burns it.
    refused = ""
    nonce_hash = verifier = ""
    with tenant_transaction(ctx):
        attempt = (SsoAttempt.objects.select_for_update()
                   .filter(cell_id=ctx.cell_id, tenant_id=ctx.tenant_id,
                           state_hash=state_hash).first())
        if (attempt is None or attempt.used_at is not None or attempt.expires_at <= now
                or attempt.provider != provider.key or attempt.redirect_uri != redirect_uri):
            refused = "state_used" if attempt and attempt.used_at else "state_invalid"
        else:
            attempt.used_at = now
            attempt.save(update_fields=["used_at", "updated_at"])
            nonce_hash = attempt.nonce_hash
            try:
                verifier = decrypt(attempt.verifier_enc, tenant_id=ctx.tenant_id,
                                   context=f"sso:{state_hash}:verifier")
            except CryptoError:
                refused = "verifier"
        if refused:
            _audit("system", "auth.sso_login", AuditOutcome.DENIED, f"{provider.key}:{refused}")
    if refused:
        raise _fail()          # after the transaction, so the audit row and the used mark are kept

    # Network calls happen outside any transaction.
    try:
        answer = http_json(provider.token_endpoint, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": provider.client_id, "client_secret": provider.client_secret,
            "code_verifier": verifier})
        id_token = answer.get("id_token")
        if not isinstance(id_token, str):
            raise SsoError
        claims = verify_id_token(id_token, provider, nonce_hash=nonce_hash, now=now)
    except SsoError:
        with tenant_transaction(ctx):
            _audit("system", "auth.sso_login", AuditOutcome.DENIED, f"{provider.key}:bad_token")
        raise _fail() from None
    return _sign_in(provider, claims, tenant, now)


def _sign_in(provider: Provider, claims: dict[str, Any], tenant: Tenant, now: dt.datetime
             ) -> LoginResult:
    ctx = build_context(tenant.id)
    subject = str(claims["sub"])
    email = str(claims.get("email") or "").strip().lower()
    email_verified = claims.get("email_verified") in (True, "true")
    result: LoginResult | None = None
    reason = "no_account"
    with tenant_transaction(ctx):
        scope = {"cell_id": ctx.cell_id, "tenant_id": ctx.tenant_id}
        link = (ExternalIdentity.objects.select_related("actor")
                .filter(provider=provider.key, subject=subject, **scope).first())
        if link is not None:
            actor = link.actor
            cred = Credential.objects.filter(actor=actor, **scope).first()
        else:
            actor, cred = None, None
            if email and email_verified:
                matches = list(Credential.objects.select_related("actor").filter(
                    email__iexact=email, email_verified_at__isnull=False, **scope)[:2])
                if len(matches) == 1 and matches[0].actor.status == Status.ACTIVE:
                    cred = matches[0]
                    actor = cred.actor
                elif len(matches) > 1:
                    reason = "ambiguous"
            elif email:
                reason = "email_unverified"
        if actor is None or cred is None:
            _audit("system", "auth.sso_login", AuditOutcome.DENIED, f"{provider.key}:{reason}")
        elif actor.status != Status.ACTIVE:
            _audit(actor.public_id, "auth.sso_login", AuditOutcome.DENIED,
                   f"{provider.key}:inactive")
        else:
            if link is None:
                try:
                    with transaction.atomic():
                        link = ExternalIdentity.objects.create(
                            actor=actor, provider=provider.key, subject=subject, email=email,
                            linked_at=now, **scope)
                except IntegrityError:                  # another sign-in linked it first
                    link = (ExternalIdentity.objects.filter(
                        provider=provider.key, subject=subject, **scope).first())
                    if link is None or link.actor_id != actor.pk:
                        link = None
                if link is not None:
                    _audit(actor.public_id, "auth.sso_link", AuditOutcome.ALLOWED, provider.key)
            if link is not None:
                link.last_login_at = now
                link.save(update_fields=["last_login_at", "updated_at"])
                cred.last_login_at = now
                cred.save(update_fields=["last_login_at", "updated_at"])
                names = organization_names()
                roles = [(r, o, names.get(o, "")) for r, o in active_roles(actor.public_id, at=now)]
                lifetime = TOKEN_LIFETIME          # "remember me" is a password-form choice only
                token = issue_token(
                    tenant_public_id=tenant.public_id, actor_public_id=actor.public_id,
                    epoch=cred.session_epoch, now=now, lifetime=lifetime)
                _audit(actor.public_id, "auth.sso_login", AuditOutcome.ALLOWED, provider.key)
                result = LoginResult(
                    token, now + lifetime, actor.public_id, actor.actor_type,
                    cred.login, tenant.public_id, tenant.name, tenant.type, roles)
    if result is None:
        raise _fail()       # after the transaction, so the audit row is kept
    return result
