"""The id that ties one request to its logs, audit rows and error responses."""
from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# A client may send its own id so it can correlate calls. We accept only a safe shape,
# so a malicious value can never reach logs or headers.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_request_id() -> str:
    return uuid.uuid4().hex


def accept_or_new(incoming: str | None) -> str:
    if incoming and _SAFE_ID.match(incoming):
        return incoming
    return new_request_id()


def set_request_id(value: str) -> Token[str | None]:
    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def get_request_id() -> str:
    return _request_id.get() or "-"


# The reason a person says they are acting (GHE-06 "why": purpose_code). It is only a CLAIM: the
# policy engine still refuses it unless the roles that person holds may act under it.
_stated_purpose: ContextVar[str | None] = ContextVar("stated_purpose", default=None)
_SAFE_PURPOSE = re.compile(r"^[A-Z_]{3,32}$")


def set_stated_purpose(incoming: str | None) -> Token[str | None]:
    return _stated_purpose.set(incoming if incoming and _SAFE_PURPOSE.match(incoming) else None)


def reset_stated_purpose(token: Token[str | None]) -> None:
    _stated_purpose.reset(token)


def get_stated_purpose() -> str | None:
    return _stated_purpose.get()


# Facts about the signed-in person that the audit record keeps (GHE-10 audit_event): set by the
# endpoint wrapper once the person is known, read when an event is written. Never a token.
_audit_extra: ContextVar[dict[str, str] | None] = ContextVar("audit_extra", default=None)


def set_audit_extra(values: dict[str, str]) -> Token[dict[str, str] | None]:
    return _audit_extra.set(values)


def reset_audit_extra(token: Token[dict[str, str] | None]) -> None:
    _audit_extra.reset(token)


def get_audit_extra() -> dict[str, str]:
    return _audit_extra.get() or {}


META_KEY = "ZOIKO_REQUEST_ID"


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every log record so log lines can be matched to a request.

    Django writes some log lines (e.g. "Internal Server Error") AFTER our middleware has
    finished, when the context variable is already cleared. Those records carry the request
    object, so we fall back to the id stored on it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = get_request_id()
        if request_id == "-":
            request = getattr(record, "request", None)
            meta = getattr(request, "META", None)
            if isinstance(meta, dict):
                request_id = str(meta.get(META_KEY, "-"))
        record.request_id = request_id
        return True
