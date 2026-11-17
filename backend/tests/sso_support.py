"""A pretend identity provider for the sign-in tests: it makes RSA keys, publishes them as a key set
and signs ID tokens, so the real verification code is exercised with nothing faked inside it."""
import base64
import datetime as dt
import json
import time
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from modules.trust import sso

ISSUER = "https://idp.example.org"
CLIENT_ID = "zoiko-client-id"
SECRET = "client-secret-value"
REDIRECT = "https://app.example.org/"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
            "n": b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
            "e": b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))}


def sign(key, header: dict[str, Any], claims: dict[str, Any]) -> str:
    head = b64(json.dumps(header).encode())
    body = b64(json.dumps(claims).encode())
    signature = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64(signature)}"


def provider() -> sso.Provider:
    return sso.Provider(
        key="acme", name="Acme ID", issuer=ISSUER, client_id=CLIENT_ID, client_secret=SECRET,
        authorization_endpoint=f"{ISSUER}/authorize", token_endpoint=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/keys")


class Idp:
    """The pretend provider. `claims()` makes a normal set of claims for a given nonce."""

    def __init__(self, kid: str = "key-1"):
        self.key = new_key()
        self.kid = kid
        self.calls: list[tuple[str, dict[str, str] | None]] = []
        self.token_answer: dict[str, Any] | None = None
        self.down = False
        self.extra_keys: list[dict[str, str]] = []

    def keyset(self) -> dict[str, Any]:
        return {"keys": [jwk(self.key, self.kid), *self.extra_keys]}

    def claims(self, nonce: str, /, now: dt.datetime | None = None, **over: Any) -> dict[str, Any]:
        moment = int((now or dt.datetime.now(dt.UTC)).timestamp()) if now else int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "subject-123", "iat": moment,
                  "exp": moment + 600, "nonce": nonce, "email": "asha@example.org",
                  "email_verified": True}
        claims.update(over)
        return {k: v for k, v in claims.items() if v is not None}

    def token(self, nonce: str, /, *, key=None, header: dict[str, Any] | None = None,
              now: dt.datetime | None = None, **over: Any) -> str:
        head = {"alg": "RS256", "typ": "JWT", "kid": self.kid, **(header or {})}
        return sign(key or self.key, head, self.claims(nonce, now, **over))

    def http_json(self, url: str, *, data: dict[str, str] | None = None, timeout: float = 5.0):
        """Stands in for `sso.http_json`: serves the key set and the token endpoint."""
        self.calls.append((url, data))
        if self.down:
            raise sso.SsoError
        if url.endswith("/keys"):
            return self.keyset()
        if url.endswith("/token"):
            if self.token_answer is None:
                raise sso.SsoError
            return self.token_answer
        raise sso.SsoError
