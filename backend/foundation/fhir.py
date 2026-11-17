"""A strict checker for the two FHIR resources the first slice uses (DEV-315, docs/FHIR-DESIGN.md).

Pinned to FHIR R4 (4.0.1). It checks the BASE specification only: required elements, the fixed
value sets, and the shape of references and codes. It does NOT yet apply any country profile (that
needs the lead's choice of country). It is deliberately narrower than the full standard: an element
this slice does not use is refused, and so are the places personal data could hide (`contained`,
the narrative `text`, `extension`). A resource that fails is never partly accepted: the caller keeps
it in quarantine and creates no clinical state from it.

Pure functions, no database and no network, so it can run anywhere and be tested on its own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

FHIR_VERSION = "4.0.1"
MAX_BYTES = 256 * 1024
MAX_DEPTH = 8
MAX_STRING = 2000

_ID = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
_REFERENCE = re.compile(
    r"^(Patient|Practitioner|PractitionerRole|Organization|ServiceRequest|"
    r"DiagnosticReport)/[A-Za-z0-9\-.]{1,64}$"
)
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_CODE = re.compile(r"^[^\s]+(\s[^\s]+)*$")

SERVICE_REQUEST_STATUS = {
    "draft",
    "active",
    "on-hold",
    "revoked",
    "completed",
    "entered-in-error",
    "unknown",
}
SERVICE_REQUEST_INTENT = {
    "proposal",
    "plan",
    "directive",
    "order",
    "original-order",
    "reflex-order",
    "filler-order",
    "instance-order",
    "option",
}
PRIORITY = {"routine", "urgent", "asap", "stat"}
REPORT_STATUS = {
    "registered",
    "partial",
    "preliminary",
    "final",
    "amended",
    "corrected",
    "appended",
    "cancelled",
    "entered-in-error",
    "unknown",
}

# The only elements each resource may carry in this slice.
_ALLOWED = {
    "ServiceRequest": {
        "resourceType",
        "id",
        "identifier",
        "status",
        "intent",
        "priority",
        "code",
        "subject",
        "requester",
        "performer",
        "authoredOn",
    },
    "DiagnosticReport": {
        "resourceType",
        "id",
        "identifier",
        "status",
        "code",
        "subject",
        "issued",
        "performer",
        "basedOn",
        "conclusion",
        "conclusionCode",
    },
}
_REQUIRED = {
    "ServiceRequest": ("status", "intent", "subject"),
    "DiagnosticReport": ("status", "code", "subject", "basedOn"),
}
# Refused outright: a place free text or hidden content could carry personal data.
_FORBIDDEN = {"contained", "text", "extension", "modifierExtension", "meta"}


@dataclass(frozen=True)
class Issue:
    path: str
    code: str  # required | value | unknown_element | forbidden | format | size
    text: str


def _issue(out: list[Issue], path: str, code: str, text: str) -> None:
    out.append(Issue(path, code, text))


def _depth(value: Any, level: int = 0) -> int:
    if isinstance(value, dict):
        return max([_depth(v, level + 1) for v in value.values()] or [level + 1])
    if isinstance(value, list):
        return max([_depth(v, level + 1) for v in value] or [level + 1])
    return level


def _check_reference(out: list[Issue], path: str, value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"reference"}:
        _issue(out, path, "format", "a reference holds only `reference`, as Type/id")
        return
    ref = value["reference"]
    if not isinstance(ref, str) or not _REFERENCE.match(ref):
        _issue(
            out,
            path,
            "format",
            "a relative reference like Patient/abc; no web address, no display name",
        )


def _check_coding(out: list[Issue], path: str, value: Any) -> None:
    """A CodeableConcept: `coding` (system and code, both required) and an optional `text`."""
    if not isinstance(value, dict) or not set(value) <= {"coding", "text"} or not value:
        _issue(out, path, "format", "a code holds `coding` and optionally `text`")
        return
    codings = value.get("coding", [])
    if not isinstance(codings, list) or len(codings) > 10:
        _issue(out, f"{path}.coding", "format", "a list of at most 10 codings")
        return
    for i, c in enumerate(codings):
        where = f"{path}.coding[{i}]"
        if not isinstance(c, dict) or not set(c) <= {"system", "code", "display"}:
            _issue(out, where, "format", "a coding holds system, code and display only")
            continue
        for needed in ("system", "code"):
            if not isinstance(c.get(needed), str) or not c[needed].strip():
                _issue(out, f"{where}.{needed}", "required", "needed so a code is never guessed")
        if isinstance(c.get("code"), str) and not _CODE.match(c["code"]):
            _issue(out, f"{where}.code", "format", "a code has no leading or trailing space")
    text = value.get("text")
    if text is not None and (not isinstance(text, str) or len(text) > 200):
        _issue(out, f"{path}.text", "format", "plain text of at most 200 characters")


def _check_identifier(out: list[Issue], path: str, value: Any) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        _issue(out, path, "format", "a list of 1 to 5 identifiers")
        return
    for i, ident in enumerate(value):
        if (
            not isinstance(ident, dict)
            or not set(ident) <= {"system", "value"}
            or not isinstance(ident.get("value"), str)
            or not 1 <= len(ident["value"]) <= 64
        ):
            _issue(
                out,
                f"{path}[{i}]",
                "format",
                "an identifier holds system and a value of 1 to 64 characters",
            )


def validate(resource: Any) -> list[Issue]:
    """Every problem found, or an empty list if the resource is acceptable in this slice."""
    out: list[Issue] = []
    if not isinstance(resource, dict):
        return [Issue("", "format", "a resource is a JSON object")]
    if len(json.dumps(resource, default=str)) > MAX_BYTES or _depth(resource) > MAX_DEPTH:
        return [Issue("", "size", "too large or too deeply nested")]
    kind = resource.get("resourceType")
    if kind not in _ALLOWED:
        return [Issue("resourceType", "value", "only ServiceRequest and DiagnosticReport")]
    for name in resource:
        if name in _FORBIDDEN:
            _issue(out, name, "forbidden", "not accepted: it could hold personal data")
        elif name not in _ALLOWED[kind]:
            _issue(out, name, "unknown_element", f"not part of the {kind} slice")
    for name in _REQUIRED[kind]:
        if name not in resource:
            _issue(out, name, "required", "required")
    if "id" in resource and (not isinstance(resource["id"], str) or not _ID.match(resource["id"])):
        _issue(out, "id", "format", "letters, digits, - and . only, up to 64 characters")
    if "identifier" in resource:
        _check_identifier(out, "identifier", resource["identifier"])
    if "subject" in resource:
        _check_reference(out, "subject", resource["subject"])
    if "code" in resource:
        _check_coding(out, "code", resource["code"])
    if kind == "ServiceRequest":
        _service_request(out, resource)
    else:
        _diagnostic_report(out, resource)
    return out


def _one_of(out: list[Issue], path: str, value: Any, allowed: set[str]) -> None:
    if value not in allowed:
        _issue(out, path, "value", f"one of {', '.join(sorted(allowed))}")


def _service_request(out: list[Issue], r: dict[str, Any]) -> None:
    if "status" in r:
        _one_of(out, "status", r["status"], SERVICE_REQUEST_STATUS)
    if "intent" in r:
        _one_of(out, "intent", r["intent"], SERVICE_REQUEST_INTENT)
    if "priority" in r:
        _one_of(out, "priority", r["priority"], PRIORITY)
    if "authoredOn" in r and (
        not isinstance(r["authoredOn"], str) or not _INSTANT.match(r["authoredOn"])
    ):
        _issue(out, "authoredOn", "format", "a date and time with a time zone")
    if "requester" in r:
        _check_reference(out, "requester", r["requester"])
    for i, p in enumerate(r.get("performer", []) if isinstance(r.get("performer"), list) else []):
        _check_reference(out, f"performer[{i}]", p)
    if "performer" in r and not isinstance(r["performer"], list):
        _issue(out, "performer", "format", "a list of references")


def _diagnostic_report(out: list[Issue], r: dict[str, Any]) -> None:
    if "status" in r:
        _one_of(out, "status", r["status"], REPORT_STATUS)
    if "issued" in r and (not isinstance(r["issued"], str) or not _INSTANT.match(r["issued"])):
        _issue(out, "issued", "format", "a date and time with a time zone")
    based = r.get("basedOn")
    if "basedOn" in r:
        if not isinstance(based, list) or len(based) != 1:
            _issue(out, "basedOn", "format", "exactly one reference to the request this answers")
        else:
            _check_reference(out, "basedOn[0]", based[0])
            if isinstance(based[0], dict) and not str(based[0].get("reference", "")).startswith(
                "ServiceRequest/"
            ):
                _issue(out, "basedOn[0]", "value", "must point at a ServiceRequest")
    if "performer" in r and (not isinstance(r["performer"], list) or len(r["performer"]) > 5):
        _issue(out, "performer", "format", "a list of at most 5 references")
    else:
        for i, p in enumerate(r.get("performer", [])):
            _check_reference(out, f"performer[{i}]", p)
    conclusion = r.get("conclusion")
    if "conclusion" in r and (
        not isinstance(conclusion, str)
        or not 1 <= len(conclusion) <= MAX_STRING
        or "\x00" in conclusion
    ):
        _issue(out, "conclusion", "format", f"plain text of 1 to {MAX_STRING} characters")
    if "conclusionCode" in r:
        codes = r["conclusionCode"]
        if not isinstance(codes, list) or len(codes) > 5:
            _issue(out, "conclusionCode", "format", "a list of at most 5 codes")
        else:
            for i, c in enumerate(codes):
                _check_coding(out, f"conclusionCode[{i}]", c)
