"""Calling AWS without an SDK (decided 27 Nov 2026: AWS KMS for the master key, SES for email, SNS
for text messages): AWS Signature Version 4 over plain HTTPS, standard library only.

Credentials come only from the server's environment (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
optionally `AWS_SESSION_TOKEN`) and the region from `AWS_REGION`. Nothing here logs a credential,
a request body or an answer; an error says only which service failed and the HTTP status.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

TIMEOUT_SECONDS = 10


class AwsError(Exception):
    """An AWS call failed. Carries the service and status only, never the answer's text."""


@dataclass(frozen=True)
class Credentials:
    access_key: str
    secret_key: str
    token: str = ""


def credentials() -> Credentials:
    access, secret = os.environ.get("AWS_ACCESS_KEY_ID", ""), os.environ.get(
        "AWS_SECRET_ACCESS_KEY", "")
    if not access or not secret:
        raise AwsError("no AWS credentials in the environment")
    return Credentials(access, secret, os.environ.get("AWS_SESSION_TOKEN", ""))


def region() -> str:
    from django.conf import settings

    found = (os.environ.get("AWS_REGION", "") or os.environ.get("AWS_DEFAULT_REGION", "")
             or str(getattr(settings, "AWS_REGION", "")))
    if not found:
        raise AwsError("no AWS region in the environment")
    return found


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def sign(
    *, method: str, url: str, headers: dict[str, str], body: bytes, service: str, area: str,
    creds: Credentials, now: dt.datetime,
) -> dict[str, str]:
    """The request's headers with the SigV4 `Authorization` (and date and token) added."""
    parts = urllib.parse.urlsplit(url)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    out = {**headers, "host": parts.netloc, "x-amz-date": stamp}
    if creds.token:
        out["x-amz-security-token"] = creds.token
    lowered = {k.lower(): " ".join(str(v).split()) for k, v in out.items()}
    names = sorted(lowered)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(query))
    canonical = "\n".join([
        method, urllib.parse.quote(parts.path or "/", safe="/-_.~"), canonical_query,
        "".join(f"{n}:{lowered[n]}\n" for n in names), ";".join(names),
        hashlib.sha256(body).hexdigest()])
    scope = f"{day}/{area}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope,
                         hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{creds.secret_key}".encode(), day), area), service),
                "aws4_request")
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={creds.access_key}/{scope}, "
                            f"SignedHeaders={';'.join(names)}, Signature={signature}")
    return out


def call(
    *, service: str, url: str, body: bytes, headers: dict[str, str], method: str = "POST",
    now: dt.datetime | None = None,
) -> bytes:
    """One signed request; the answer's body. Raises AwsError on any failure."""
    area = region()
    signed = sign(method=method, url=url, headers=headers, body=body, service=service,
                  area=area, creds=credentials(), now=now or dt.datetime.now(dt.timezone.utc))
    if not url.startswith("https://"):
        raise AwsError(f"{service}: only https is allowed")
    request = urllib.request.Request(url, data=body, method=method, headers=signed)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as answer:  # noqa: S310
            return bytes(answer.read())
    except urllib.error.HTTPError as exc:
        raise AwsError(f"{service} answered {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AwsError(f"{service} could not be reached") from None


def json_call(service: str, target: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The JSON-1.1 protocol (KMS): `X-Amz-Target` names the operation."""
    host = f"{service}.{region()}.amazonaws.com"
    raw = call(service=service, url=f"https://{host}/", body=json.dumps(payload).encode(),
               headers={"Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": target})
    try:
        found = json.loads(raw)
    except ValueError:
        raise AwsError(f"{service} gave an unreadable answer") from None
    if not isinstance(found, dict):
        raise AwsError(f"{service} gave an unreadable answer")
    return found


def kms_decrypt(blob_b64: str) -> bytes:
    """Unwrap a data key KMS encrypted (its CiphertextBlob, base64). The plaintext key stays in
    this process's memory only."""
    import base64

    found = json_call("kms", "TrentService.Decrypt", {"CiphertextBlob": blob_b64})
    try:
        return base64.b64decode(str(found["Plaintext"]), validate=True)
    except (KeyError, ValueError):
        raise AwsError("kms gave an unreadable answer") from None
