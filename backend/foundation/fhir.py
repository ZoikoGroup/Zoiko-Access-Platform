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

from foundation.identifiers import contains_nhs_number

FHIR_VERSION = "4.0.1"

# UK Core (decided 18 Nov 2026, DEV-316/DEV-319). Only the rules we are sure of are checked; the
# full profile still has to be confirmed against the published UK Core package before it is relied
# on (docs/FHIR-DESIGN.md).
UK_CORE = "uk-core"
SNOMED = "http://snomed.info/sct"
# The published UK Core release these rules were checked against (DEV-342): package
# fhir.r4.ukcore.stu2 2.0.2 (UKCoreServiceRequest 2.5.0, UKCoreDiagnosticReportLab 2.0.0,
# UKCoreObservationLab 2.0.0).
UK_CORE_PACKAGE = "fhir.r4.ukcore.stu2#2.0.2"
# A lab report and a lab value each say they are laboratory ones (UK Core Lab profiles, min 1).
LAB_REPORT_CATEGORY = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                                   "code": "LAB"}]}
LAB_VALUE_CATEGORY = {"coding": [{
    "system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}
# An imaging report (DEV-348, LEAD DECISION: imaging reports only, through the lab flow): the
# report says RAD, a measured value says "imaging". No images are taken in.
IMAGING_REPORT_CATEGORY = {"coding": [{
    "system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "RAD"}]}
IMAGING_VALUE_CATEGORY = {"coding": [{
    "system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "imaging"}]}
NHS_NUMBER_SYSTEM = "https://fhir.nhs.uk/Id/nhs-number"
UK_CORE_PROFILES = {
    "ServiceRequest": "https://fhir.hl7.org.uk/StructureDefinition/UKCore-ServiceRequest",
    "DiagnosticReport": "https://fhir.hl7.org.uk/StructureDefinition/UKCore-DiagnosticReport",
}
_SCTID = re.compile(r"^[1-9][0-9]{5,17}$")
MAX_BYTES = 256 * 1024
MAX_DEPTH = 8
MAX_STRING = 2000

_ID = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
_REFERENCE = re.compile(
    r"^(Patient|Practitioner|PractitionerRole|Organization|ServiceRequest|"
    r"DiagnosticReport|Observation)/[A-Za-z0-9\-.]{1,64}$"
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
        "category",
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
        "category",
        "code",
        "subject",
        "issued",
        "performer",
        "basedOn",
        "conclusion",
        "conclusionCode",
        "result",
        "presentedForm",  # the lab's PDF report (DEV-349)
    },
    # One measured value of a lab result (DEV-330): a quantity or a short text, with its normal
    # range and the lab's interpretation. Nothing else.
    "Observation": {
        "resourceType",
        "id",
        "status",
        "category",
        "code",
        "subject",
        "valueQuantity",
        "valueString",
        "referenceRange",
        "interpretation",
    },
}
_REQUIRED = {
    "ServiceRequest": ("status", "intent", "subject"),
    "DiagnosticReport": ("status", "code", "subject", "basedOn"),
    "Observation": ("status", "code", "subject"),
}
OBSERVATION_STATUS = {
    "registered", "preliminary", "final", "amended", "corrected", "cancelled",
    "entered-in-error", "unknown",
}
MAX_VALUES = 50
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


def verhoeff_ok(number: str) -> bool:
    """The Verhoeff check digit that every SNOMED CT identifier carries in its last digit."""
    d = [
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
        [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
        [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
        [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
        [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
        [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
        [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
        [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
        [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
    ]
    p = [
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
        [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
        [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
        [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
        [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
        [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
        [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
    ]
    check = 0
    for i, ch in enumerate(reversed(number)):
        check = d[check][p[i % 8][int(ch)]]
    return check == 0


def to_fhir_id(public_id: str) -> str:
    """A Zoiko public id as a FHIR id (DEV-331). Our ids are URL-safe base64 (letters, digits, -
    and _); a FHIR id may not hold "_", so it is written as ".", which our ids never contain. The
    change can always be undone with `from_fhir_id`."""
    return public_id.replace("_", ".")


def from_fhir_id(fhir_id: str) -> str:
    """The Zoiko public id behind a FHIR id made by `to_fhir_id`."""
    return fhir_id.replace(".", "_")


def sctid_ok(code: str) -> bool:
    """A SNOMED CT identifier: 6 to 18 digits, no leading zero, a valid Verhoeff check digit, and a
    partition identifier (the two digits before the check digit) of a concept: 00 (short form)
    or 10 (long form). A description or relationship id is not a code."""
    if not _SCTID.match(code) or not verhoeff_ok(code):
        return False
    return code[-3:-1] in {"00", "10"}  # a concept (short or long form)


def _codings(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("coding"), list):
        return [c for c in value["coding"] if isinstance(c, dict)]
    return []


def _uk_core(out: list[Issue], kind: str, r: dict[str, Any]) -> None:
    """The UK Core rules this slice is sure of. A national number never travels in a resource
    (Zoiko's own rule, DEV-222): the partner learns a patient only through the agreement flow."""
    text = json.dumps(r, default=str)
    if NHS_NUMBER_SYSTEM in text or contains_nhs_number(text):
        _issue(out, "", "forbidden", "an NHS number is never accepted in a message")
    codes = _codings(r.get("code"))
    # UK Core only PREFERS SNOMED CT here; requiring it is Zoiko's own stricter rule (DEV-321).
    if "code" in r and not any(c.get("system") == SNOMED for c in codes):
        _issue(out, "code", "required", "UK Core needs a SNOMED CT code")
    accepted = {
        "DiagnosticReport": (LAB_REPORT_CATEGORY, IMAGING_REPORT_CATEGORY),
        "Observation": (LAB_VALUE_CATEGORY, IMAGING_VALUE_CATEGORY),
    }.get(kind)
    if accepted is not None:
        wants = [(c["coding"][0]["system"], c["coding"][0]["code"]) for c in accepted]
        given = r.get("category")
        cats: list[Any] = given if isinstance(given, list) else []
        if not any((c.get("system"), c.get("code")) in wants
                   for cat in cats for c in _codings(cat)):
            names = " or ".join(code for _, code in wants)
            _issue(out, "category", "required",
                   f"UK Core needs category {names} ({UK_CORE_PACKAGE})")
    places = [("code", r.get("code"))]
    if kind == "DiagnosticReport":
        places += [
            (f"conclusionCode[{i}]", c)
            for i, c in enumerate(r.get("conclusionCode") or [])
            if isinstance(r.get("conclusionCode"), list)
        ]
    for where, concept in places:
        for i, c in enumerate(_codings(concept)):
            if c.get("system") == SNOMED and not (
                isinstance(c.get("code"), str) and sctid_ok(c["code"])
            ):
                _issue(
                    out, f"{where}.coding[{i}].code", "format", "not a valid SNOMED CT identifier"
                )


def validate(resource: Any, profile: str | None = None) -> list[Issue]:
    """Every problem found, or an empty list if the resource is acceptable in this slice. With
    `profile=UK_CORE` the UK Core rules are checked as well."""
    out: list[Issue] = []
    if profile not in (None, UK_CORE):
        return [Issue("", "value", "unknown profile")]
    if not isinstance(resource, dict):
        return [Issue("", "format", "a resource is a JSON object")]
    if len(json.dumps(_without_pdf_data(resource), default=str)) > MAX_BYTES or \
            _depth(resource) > MAX_DEPTH:
        return [Issue("", "size", "too large or too deeply nested")]
    kind = resource.get("resourceType")
    if kind == "Bundle":
        return _bundle(resource, profile)
    if kind not in _ALLOWED:
        return [Issue("resourceType", "value",
                      "only ServiceRequest, DiagnosticReport, Observation or a Bundle")]
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
    if "category" in resource:
        cats = resource["category"]
        if not isinstance(cats, list) or not 1 <= len(cats) <= 5:
            _issue(out, "category", "format", "a list of 1 to 5 codes")
        else:
            for i, c in enumerate(cats):
                _check_coding(out, f"category[{i}]", c)
    if kind == "ServiceRequest":
        _service_request(out, resource)
    elif kind == "Observation":
        _observation(out, resource)
    else:
        _diagnostic_report(out, resource)
    if profile == UK_CORE:
        _uk_core(out, kind, resource)
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


MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_FORMS = 5


def _without_pdf_data(resource: Any) -> Any:
    """The resource with any presentedForm data left out, so the size rule measures the message
    itself; each PDF has its own limit (DEV-349)."""
    if not isinstance(resource, dict):
        return resource
    if resource.get("resourceType") == "Bundle" and isinstance(resource.get("entry"), list):
        return {**resource, "entry": [
            {**e, "resource": _without_pdf_data(e.get("resource"))} if isinstance(e, dict) else e
            for e in resource["entry"]]}
    forms = resource.get("presentedForm")
    if isinstance(forms, list):
        return {**resource, "presentedForm": [
            {k: v for k, v in f.items() if k != "data"} if isinstance(f, dict) else f
            for f in forms]}
    return resource


def pdf_forms(report: dict[str, Any]) -> list[tuple[str, bytes]]:
    """The PDFs a valid report carries: (title, bytes)."""
    import base64

    out = []
    for f in report.get("presentedForm") or []:
        out.append((str(f.get("title") or "report.pdf"), base64.b64decode(f["data"])))
    return out


def _check_forms(out: list[Issue], forms: Any) -> None:
    import base64
    import binascii

    if not isinstance(forms, list) or not 1 <= len(forms) <= MAX_FORMS:
        _issue(out, "presentedForm", "format", f"a list of 1 to {MAX_FORMS} attachments")
        return
    for i, f in enumerate(forms):
        where = f"presentedForm[{i}]"
        if not isinstance(f, dict) or not set(f) <= {"contentType", "data", "title"}:
            _issue(out, where, "format", "an attachment holds contentType, data and title only")
            continue
        if f.get("contentType") != "application/pdf":
            _issue(out, f"{where}.contentType", "value", "application/pdf only")
        title = f.get("title")
        if title is not None and (not isinstance(title, str) or not 1 <= len(title) <= 100):
            _issue(out, f"{where}.title", "format", "text of 1 to 100 characters")
        data = f.get("data")
        try:
            raw = base64.b64decode(data, validate=True) if isinstance(data, str) else b""
        except (binascii.Error, ValueError):
            raw = b""
        if not 1 <= len(raw) <= MAX_PDF_BYTES or not raw.startswith(b"%PDF-"):
            _issue(out, f"{where}.data", "format", "a PDF of at most 5 MB, base64")


def _diagnostic_report(out: list[Issue], r: dict[str, Any]) -> None:
    if "presentedForm" in r:
        _check_forms(out, r["presentedForm"])
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


def codings_in(resource: Any) -> list[dict[str, str]]:
    """Every coding (system, code, display) in a resource's code and conclusion codes, for the code
    mapping review list (DEV-322). Anything not shaped like a coding is skipped."""
    if not isinstance(resource, dict):
        return []
    if resource.get("resourceType") == "Bundle" and isinstance(resource.get("entry"), list):
        return [c for e in resource["entry"] if isinstance(e, dict)
                for c in codings_in(e.get("resource"))]  # DEV-340: each report and value
    concepts = [resource.get("code")]
    if isinstance(resource.get("conclusionCode"), list):
        concepts += resource["conclusionCode"]
    out: list[dict[str, str]] = []
    for concept in concepts:
        for c in _codings(concept):
            if isinstance(c.get("system"), str) and isinstance(c.get("code"), str):
                out.append(
                    {
                        "system": c["system"],
                        "code": c["code"],
                        "display": str(c.get("display") or ""),
                    }
                )
    return out


def service_request(
    *,
    request_ref: str,
    order_no: str,
    kind: str,
    urgency: str,
    snomed_code: str,
    label: str,
    authored_on: str,
) -> dict[str, Any]:
    """The FHIR ServiceRequest Zoiko sends a lab's own system for one order (DEV-323). References
    only: the order number, what is asked (a SNOMED CT code with its plain words), the urgency and
    a per-order subject that names nobody. The patient's name and birth date are never in it; the
    lab learns them only through the patient's agreement (DEV-300). The result always passes the UK
    Core checks above, or this raises ValueError."""
    resource = {
        "resourceType": "ServiceRequest",
        "id": to_fhir_id(request_ref),
        "identifier": [{"system": "urn:zoiko:order", "value": order_no}],
        "status": "active",
        "intent": "order",
        "priority": "urgent" if urgency == "urgent" else "routine",
        "code": {"coding": [{"system": SNOMED, "code": snomed_code}], "text": label[:200]},
        "subject": {"reference": f"Patient/{to_fhir_id(request_ref)}"},
        "authoredOn": authored_on,
    }
    problems = validate(resource, profile=UK_CORE)
    if problems or kind not in ("lab_order", "pharmacy_request", "homecare_referral"):
        raise ValueError([f"{p.path}:{p.code}" for p in problems] or ["kind"])
    return resource


# ------------------------------------------------------------------ result values (DEV-330)
_NUMBER = (int, float)
INTERPRETATION_FLAG = {
    "n": "normal", "a": "abnormal", "h": "high", "l": "low",
    "hh": "critical", "ll": "critical", "aa": "critical", "crit": "critical",
}


def _quantity(out: list[Issue], path: str, q: Any, *, needs_value: bool) -> None:
    if not isinstance(q, dict) or not set(q) <= {"value", "unit", "system", "code"}:
        _issue(out, path, "format", "a quantity holds value, unit, system and code only")
        return
    value = q.get("value")
    if (needs_value or "value" in q) and (
        isinstance(value, bool) or not isinstance(value, _NUMBER)
    ):
        _issue(out, f"{path}.value", "format", "a number")
    for name in ("unit", "system", "code"):
        v = q.get(name)
        if v is not None and (not isinstance(v, str) or not 1 <= len(v) <= 40):
            _issue(out, f"{path}.{name}", "format", "text of 1 to 40 characters")


def _observation(out: list[Issue], r: dict[str, Any]) -> None:
    if "status" in r:
        _one_of(out, "status", r["status"], OBSERVATION_STATUS)
    has_q, has_s = "valueQuantity" in r, "valueString" in r
    if has_q == has_s:
        _issue(out, "value[x]", "required", "exactly one of valueQuantity or valueString")
    if has_q:
        _quantity(out, "valueQuantity", r["valueQuantity"], needs_value=True)
    if has_s and (not isinstance(r["valueString"], str) or not 1 <= len(r["valueString"]) <= 80
                  or any(ord(ch) < 32 for ch in r["valueString"])):
        _issue(out, "valueString", "format", "plain text of 1 to 80 characters")
    ranges = r.get("referenceRange")
    if "referenceRange" in r:
        if not isinstance(ranges, list) or len(ranges) > 2:
            _issue(out, "referenceRange", "format", "a list of at most 2 ranges")
        else:
            for i, rr in enumerate(ranges):
                where = f"referenceRange[{i}]"
                if not isinstance(rr, dict) or not rr or not set(rr) <= {"low", "high"}:
                    _issue(out, where, "format", "a range holds low and high only")
                    continue
                for end in ("low", "high"):
                    if end in rr:
                        _quantity(out, f"{where}.{end}", rr[end], needs_value=True)
    if "interpretation" in r:
        codes = r["interpretation"]
        if not isinstance(codes, list) or len(codes) > 3:
            _issue(out, "interpretation", "format", "a list of at most 3 codes")
        else:
            for i, c in enumerate(codes):
                _check_coding(out, f"interpretation[{i}]", c)


def _bundle(bundle: dict[str, Any], profile: str | None) -> list[Issue]:
    """A Bundle of type `collection`: exactly one DiagnosticReport and the Observations its
    `result` lists, every one of them, about the same subject (DEV-330)."""
    out: list[Issue] = []
    for name in bundle:
        if name not in {"resourceType", "id", "type", "entry"}:
            _issue(out, name, "unknown_element", "a Bundle holds id, type and entry only")
    if bundle.get("type") != "collection":
        _issue(out, "type", "value", "collection")
    entries = bundle.get("entry")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_VALUES + 1:
        _issue(out, "entry", "format", f"1 to {MAX_VALUES + 1} entries")
        return out
    resources: list[Any] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or set(e) != {"resource"}:
            _issue(out, f"entry[{i}]", "format", "an entry holds `resource` only")
            continue
        r = e["resource"]
        kind = r.get("resourceType") if isinstance(r, dict) else None
        if kind not in ("DiagnosticReport", "Observation"):
            _issue(out, f"entry[{i}].resource", "value", "a DiagnosticReport or an Observation")
            continue
        out += [Issue(f"entry[{i}].{p.path}".rstrip("."), p.code, p.text)
                for p in validate(r, profile)]
        resources.append(r)
    reports = [r for r in resources if r.get("resourceType") == "DiagnosticReport"]
    if len(reports) != 1:
        _issue(out, "entry", "value", "exactly one DiagnosticReport")
        return out
    report = reports[0]
    observations = {
        str(r.get("id")): r for r in resources if r.get("resourceType") == "Observation"
    }
    listed = [str(x.get("reference", ""))[len("Observation/"):]
              for x in (report.get("result") or []) if isinstance(x, dict)]
    if "result" in report and (not isinstance(report["result"], list)
                               or len(report["result"]) > MAX_VALUES):
        _issue(out, "result", "format", f"a list of at most {MAX_VALUES} references")
    if sorted(listed) != sorted(observations) or len(set(listed)) != len(listed):
        _issue(out, "result", "value", "lists every Observation in the Bundle exactly once")
    subject = report.get("subject")
    if any(o.get("subject") != subject for o in observations.values()):
        _issue(out, "entry", "value", "every value is about the report's own subject")
    return out


def report_of(resource: dict[str, Any]) -> dict[str, Any]:
    """The DiagnosticReport of a valid single report or Bundle."""
    if resource.get("resourceType") != "Bundle":
        return resource
    return next(e["resource"] for e in resource["entry"]
                if e["resource"].get("resourceType") == "DiagnosticReport")


def values_of(resource: dict[str, Any]) -> list[dict[str, Any]]:
    """The measured values of a valid Bundle, in the order the report lists them: name, SNOMED CT
    code, value, unit, normal range and the lab's flag. A single report has none."""
    if resource.get("resourceType") != "Bundle":
        return []
    report = report_of(resource)
    by_id = {e["resource"].get("id"): e["resource"] for e in resource["entry"]
             if e["resource"].get("resourceType") == "Observation"}
    out = []
    for ref in report.get("result") or []:
        o = by_id[ref["reference"][len("Observation/"):]]
        codes = _codings(o.get("code"))
        snomed = next((c["code"] for c in codes if c.get("system") == SNOMED), "")
        name = (o.get("code", {}).get("text") or next(
            (c.get("display") for c in codes if c.get("display")), "") or snomed)[:80]
        q = o.get("valueQuantity") or {}
        rng = (o.get("referenceRange") or [{}])[0]
        flag = ""
        for concept in o.get("interpretation") or []:
            for c in _codings(concept):
                flag = INTERPRETATION_FLAG.get(str(c.get("code", "")).lower(), flag)
        out.append({
            "name": name,
            "code": snomed,
            "value": q.get("value") if q else o.get("valueString", ""),
            "unit": str(q.get("unit") or q.get("code") or "")[:20] if q else "",
            "low": (rng.get("low") or {}).get("value"),
            "high": (rng.get("high") or {}).get("value"),
            "flag": flag,
        })
    return out
