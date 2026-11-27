"""Plugging partner adapters in by configuration, not by changing the product (DEV-361).

Two ways, both read once at start-up:

  CONNECTOR_PLUGINS   "package.module:attribute, ..."  An adapter someone wrote for one partner
                      (a FHIR endpoint, an HL7 feed, a private API). The attribute is an adapter
                      object, a class, or a function returning one. It must have a `key`, say
                      whether it is `idempotent`, and have `submit` and `query`.
  CONNECTOR_HTTP      a JSON list of {"key", "url", "secret", "profile_version"?}. A partner that
                      speaks Zoiko's own simple JSON contract needs no code at all (HttpAdapter).

Anything that does not load is written down with the reason, not raised: one broken partner never
stops the server. The platform's Partner systems page shows where each adapter came from, and what
failed. A plugged-in adapter is still only usable once its version is certified and live.

Zoiko's JSON contract (HttpAdapter):
  POST {url}/operations           the operation; header Idempotency-Key, X-Zoiko-Signature
  GET  {url}/operations/{ref}     what became of it
  The partner answers {"outcome": "accepted|confirmed|rejected|pending|not_found",
  "partner_ref"?, "code"?}. HTTP 501 or {"outcome": "unsupported"} halts the connector.
  X-Zoiko-Signature is "sha256=" + HMAC-SHA256(secret, the exact body sent).
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from typing import Any

from django.conf import settings

from . import adapters
from .adapters import (
    AdapterTimeout,
    AdapterUnavailable,
    AdapterUnsupported,
    OperationView,
    Outcome,
    PartnerResult,
)

_KEY = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
_CODE = re.compile(r"^[A-Za-z0-9._:-]{1,40}$")
TIMEOUT_SECONDS = 10
MAX_ANSWER = 64_000

_sources: dict[str, str] = {}
_problems: list[dict[str, str]] = []


def sources() -> dict[str, str]:
    """Where each adapter came from: built_in, plugin or http."""
    return {key: _sources.get(key, "built_in") for key in adapters.registered()}


def problems() -> list[dict[str, str]]:
    """Adapters the configuration named that did not load, and why. Never a secret."""
    return list(_problems)


def _https_ok(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.netloc:
        return True
    return bool(getattr(settings, "CONNECTOR_ALLOW_INSECURE", False)) and (
        parts.scheme == "http" and bool(parts.netloc))


class HttpAdapter:
    """A partner that speaks Zoiko's simple JSON contract (see the module's description)."""

    idempotent = True

    def __init__(self, key: str, url: str, secret: str, profile_version: str = "1") -> None:
        self.key, self.url, self._secret = key, url.rstrip("/"), secret
        self.profile_version = profile_version

    def _call(self, method: str, path: str, body: bytes | None, key: str = "") -> PartnerResult:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers.update({
                "Content-Type": "application/json", "Idempotency-Key": key,
                "X-Zoiko-Signature": "sha256=" + hmac.new(
                    self._secret.encode(), body, hashlib.sha256).hexdigest()})
        request = urllib.request.Request(  # noqa: S310 - the scheme is checked when loading
            self.url + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                raw = response.read(MAX_ANSWER + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 501:
                raise AdapterUnsupported from None
            if exc.code in (502, 503, 504):
                raise AdapterUnavailable from None    # the partner said it did not take it
            raise AdapterTimeout from None            # anything else: we cannot be sure
        except TimeoutError:
            raise AdapterTimeout from None
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise AdapterTimeout from None
            raise AdapterUnavailable from None        # refused: it never got there
        return self._read(raw)

    @staticmethod
    def _read(raw: bytes) -> PartnerResult:
        """A clear answer, or "we do not know": never a guess."""
        try:
            data = json.loads(raw) if len(raw) <= MAX_ANSWER else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise AdapterTimeout
        outcome = data.get("outcome")
        if outcome == "unsupported":
            raise AdapterUnsupported
        try:
            found = Outcome(outcome)
        except ValueError:
            raise AdapterTimeout from None
        ref = data.get("partner_ref") or ""
        code = data.get("code") or ""
        if not isinstance(ref, str) or len(ref) > 64:
            ref = ""
        if not isinstance(code, str) or not _CODE.match(code):
            code = ""
        return PartnerResult(found, ref, code)

    def submit(self, operation: OperationView) -> PartnerResult:
        body = json.dumps(asdict(operation), separators=(",", ":"), sort_keys=True).encode()
        return self._call("POST", "/operations", body, operation.idempotency_key)

    def query(self, operation: OperationView) -> PartnerResult:
        ref = urllib.parse.quote(operation.operation_ref, safe="")
        return self._call("GET", f"/operations/{ref}", None)


def _valid(found: Any) -> str:
    """Why this is not a usable adapter, or "" when it is."""
    key = getattr(found, "key", None)
    if not isinstance(key, str) or not _KEY.match(key):
        return "it has no usable key (small letters, digits, - or _)"
    if not isinstance(getattr(found, "idempotent", None), bool):
        return "it does not say whether it is idempotent"
    if not callable(getattr(found, "submit", None)) or not callable(getattr(found, "query", None)):
        return "it has no submit and query"
    if key in adapters.registered() and _sources.get(key) != "built_in":
        return f"the key {key} is already used"
    return ""


def _add(found: Any, source: str, named: str) -> None:
    why = _valid(found)
    if not why:
        try:
            adapters.register(found)
        except RuntimeError as exc:                   # a pretend partner outside development
            why = str(exc)
    if why:
        _problems.append({"name": named, "reason": why})
        return
    _sources[found.key] = source


def load_configured() -> None:
    """Load what the settings name. Safe to call again (tests): it starts from the settings."""
    for key in [k for k, s in _sources.items() if s != "built_in"]:
        adapters._adapters.pop(key, None)
    _sources.clear()
    _problems.clear()
    raw = str(getattr(settings, "CONNECTOR_PLUGINS", "") or "")
    for named in [n.strip() for n in raw.split(",") if n.strip()]:
        module_name, _, attribute = named.partition(":")
        try:
            target = getattr(importlib.import_module(module_name), attribute or "adapter")
            found = target() if isinstance(target, type) or (
                callable(target) and not hasattr(target, "submit")) else target
        except Exception as exc:  # noqa: BLE001 - a broken plug-in must not stop the server
            why = f"it could not be loaded ({type(exc).__name__})"
            _problems.append({"name": named, "reason": why})
            continue
        _add(found, "plugin", named)
    configured = getattr(settings, "CONNECTOR_HTTP", "") or ""
    try:
        items = json.loads(configured) if isinstance(configured, str) and configured else (
            list(configured) if configured else [])
    except ValueError:
        _problems.append({"name": "CONNECTOR_HTTP", "reason": "it is not valid JSON"})
        items = []
    for item in items:
        name = str(item.get("key", "?")) if isinstance(item, dict) else "?"
        if not isinstance(item, dict) or not all(
                isinstance(item.get(k), str) and item.get(k) for k in ("key", "url", "secret")):
            _problems.append({"name": name, "reason": "it needs a key, a url and a secret"})
            continue
        if not _https_ok(item["url"]):
            _problems.append({"name": name, "reason": "its address is not https"})
            continue
        _add(HttpAdapter(item["key"], item["url"], item["secret"],
                         str(item.get("profile_version") or "1")[:32]), "http", name)
