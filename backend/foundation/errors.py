"""The error vocabulary. Pure Python (no Django), so every module can use it.

Design rule: there is NO free-text path. An error is a stable code plus, optionally, the names of
the fields that failed. The text a client sees comes only from the fixed catalog below, so
personal or clinical data cannot leak into an error message by accident.

Codes come from GHE-11 W17 and GHE-12; HTTP statuses 403/409/422/423 follow GHE-07 and GHE-11.
Other statuses are our choice (see docs/decisions/REGISTER.md).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    # Request problems
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    # Policy and safety (GHE-11 W17)
    POLICY_DENIED = "POLICY_DENIED"
    JURISDICTION_INACTIVE = "JURISDICTION_INACTIVE"
    SUBJECT_LINK_UNVERIFIED = "SUBJECT_LINK_UNVERIFIED"
    HOLD_ACTIVE = "HOLD_ACTIVE"
    # Concurrency, idempotency, evidence (GHE-11 W17)
    CONFLICT_VERSION = "CONFLICT_VERSION"
    DUPLICATE_COMMAND = "DUPLICATE_COMMAND"
    UNSAFE_REPLAY = "UNSAFE_REPLAY"
    PARTNER_UNCONFIRMED = "PARTNER_UNCONFIRMED"
    CLINICAL_ACCEPTANCE_ABSENT = "CLINICAL_ACCEPTANCE_ABSENT"
    # Platform
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


@dataclass(frozen=True)
class ErrorSpec:
    status: int
    title: str
    detail: str
    retryable: bool  # may the SAME request succeed if tried again later?


CATALOG: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.VALIDATION_FAILED: ErrorSpec(
        422, "Validation failed", "The request could not be processed as submitted.", False),
    ErrorCode.NOT_FOUND: ErrorSpec(
        404, "Not found", "The requested resource was not found.", False),
    ErrorCode.UNAUTHENTICATED: ErrorSpec(
        401, "Authentication required", "Valid authentication is required.", False),
    ErrorCode.STEP_UP_REQUIRED: ErrorSpec(
        401, "Additional verification required", "Stronger authentication is required.", False),
    ErrorCode.RATE_LIMITED: ErrorSpec(
        429, "Too many requests", "Too many requests. Try again later.", True),
    ErrorCode.POLICY_DENIED: ErrorSpec(
        403, "Not permitted", "This action is not permitted.", False),
    ErrorCode.JURISDICTION_INACTIVE: ErrorSpec(
        403, "Service not available", "This service is not available in this location.", False),
    ErrorCode.SUBJECT_LINK_UNVERIFIED: ErrorSpec(
        403, "Identity not verified", "The identity link could not be verified.", False),
    ErrorCode.HOLD_ACTIVE: ErrorSpec(
        423, "Temporarily blocked", "This action is temporarily blocked.", False),
    ErrorCode.CONFLICT_VERSION: ErrorSpec(
        409, "Version conflict", "The record changed. Reload and try again.", True),
    ErrorCode.DUPLICATE_COMMAND: ErrorSpec(
        409, "Duplicate request", "This request conflicts with an earlier request.", False),
    ErrorCode.UNSAFE_REPLAY: ErrorSpec(
        409, "Replay not allowed", "This operation cannot be safely repeated.", False),
    ErrorCode.PARTNER_UNCONFIRMED: ErrorSpec(
        409, "Awaiting partner", "The partner has not confirmed this yet.", False),
    ErrorCode.CLINICAL_ACCEPTANCE_ABSENT: ErrorSpec(
        409, "Clinical acceptance missing", "Required clinical acceptance is not recorded.", False),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(
        500, "Internal error", "An internal error occurred.", False),
    ErrorCode.SERVICE_UNAVAILABLE: ErrorSpec(
        503, "Service unavailable", "The service is temporarily unavailable.", True),
}

# Field names only: lowercase letters, digits, underscore, dot (e.g. "address.postcode").
# Anything else (spaces, '=', quotes) could be data, so it is refused.
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")


class ZoikoError(Exception):
    """Raise this from services and domain code. Carries a code, never free text."""

    def __init__(self, code: ErrorCode, *, fields: Iterable[str] = ()) -> None:
        names = tuple(fields)
        for name in names:
            if not _FIELD_NAME.match(name):
                raise ValueError("fields must be plain field names, not data")
        super().__init__(code.value)  # the exception text is ONLY the code
        self.code = code
        self.fields = names

    @property
    def spec(self) -> ErrorSpec:
        return CATALOG[self.code]


def problem_body(code: ErrorCode, request_id: str, fields: Iterable[str] = ()) -> dict[str, object]:
    """RFC 9457 'problem details' body. Every value comes from the catalog or the request id."""
    spec = CATALOG[code]
    body: dict[str, object] = {
        "type": f"urn:zoiko:error:{code.value}",
        "title": spec.title,
        "status": spec.status,
        "detail": spec.detail,
        "code": code.value,
        "request_id": request_id,
        "retryable": spec.retryable,
    }
    names = list(fields)
    if names:
        body["fields"] = names
    return body
