"""Synthetic patients and example FHIR messages for a partner developer's sandbox (DEV-329).

Everything here is made up and fixed. Nothing is stored and nothing is read from the database, so
no real person can ever appear. Every synthetic patient says so in its id and its name, and each
birth date is in the far past or far future of a real patient list (1900 or 2099), so a synthetic
record can never be mistaken for, or matched to, a real one (the identity matching rules ignore
neither, but a clinic never holds these people).

The examples are built with the same code Zoiko uses for real messages and pass the same UK Core
checks (foundation/fhir.py), so what works here works in the clinic's intake.
"""

from __future__ import annotations

from typing import Any

from . import fhir

PREFIX = "synthetic-"

# Scenarios a developer needs to try: a normal, an abnormal, a critical result, a correction, and a
# message that must be held. Names are plainly not real.
PATIENTS: tuple[dict[str, str], ...] = (
    {"id": "synthetic-001", "name": "Synthetic Testpatient Alpha", "birth_date": "1900-01-01",
     "scenario": "normal"},
    {"id": "synthetic-002", "name": "Synthetic Testpatient Bravo", "birth_date": "1900-02-02",
     "scenario": "abnormal"},
    {"id": "synthetic-003", "name": "Synthetic Testpatient Charlie", "birth_date": "1900-03-03",
     "scenario": "critical"},
    {"id": "synthetic-004", "name": "Synthetic Testpatient Delta", "birth_date": "2099-04-04",
     "scenario": "corrected"},
    {"id": "synthetic-005", "name": "Synthetic Testpatient Echo", "birth_date": "2099-05-05",
     "scenario": "held"},
)

TESTS = {
    "26604007": "Full blood count",
    "27171005": "Urinalysis",
}

_INTERPRETATION = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
_RESULT = {
    "normal": ("N", "final", "All values within the reference range."),
    "abnormal": ("A", "final", "Haemoglobin below the reference range."),
    "critical": ("CRIT", "final", "Potassium far above the reference range."),
    "corrected": ("N", "corrected", "Corrected: the earlier value came from another sample."),
}


def _order_ref(patient: dict[str, str]) -> str:
    return f"{patient['id']}-order"


def example_order(patient: dict[str, str], code: str = "26604007") -> dict[str, Any]:
    """The ServiceRequest Zoiko would send for this synthetic patient."""
    return fhir.service_request(
        request_ref=_order_ref(patient),
        order_no=f"SYN-{patient['id'][-3:]}",
        kind="lab_order",
        urgency="urgent" if patient["scenario"] == "critical" else "routine",
        snomed_code=code,
        label=TESTS[code],
        authored_on="2026-01-01T09:00:00Z",
    )


def example_report(patient: dict[str, str], code: str = "26604007") -> dict[str, Any]:
    """The DiagnosticReport a lab would send back. The "held" scenario is deliberately coded only
    in a local system, with no SNOMED CT code, so it shows a message the intake holds."""
    scenario = patient["scenario"]
    flag, status, text = _RESULT.get(scenario, _RESULT["normal"])
    coding = (
        [{"system": "http://example.org/local-lab-codes", "code": "FBC-01"}]
        if scenario == "held"
        else [{"system": fhir.SNOMED, "code": code}]
    )
    return {
        "resourceType": "DiagnosticReport",
        "id": f"{patient['id']}-report",
        "category": [fhir.LAB_REPORT_CATEGORY],
        "status": status,
        "code": {"coding": coding},
        "subject": {"reference": f"Patient/{_order_ref(patient)}"},
        "basedOn": [{"reference": f"ServiceRequest/{_order_ref(patient)}"}],
        "conclusion": text,
        "conclusionCode": [{"coding": [{"system": _INTERPRETATION, "code": flag}]}],
    }


def sandbox() -> list[dict[str, Any]]:
    """Every synthetic patient with its example order and report, and what the intake would say."""
    out = []
    for p in PATIENTS:
        report = example_report(p)
        out.append(
            {
                **p,
                "order": example_order(p),
                "report": report,
                "expect": "held" if fhir.validate(report, profile=fhir.UK_CORE) else "accepted",
            }
        )
    return out


def check(resource: Any) -> dict[str, Any]:
    """Check a developer's own resource against the same rules as the intake. Nothing is kept.
    Says whether it would be accepted or held, and why."""
    issues = fhir.validate(resource, profile=fhir.UK_CORE)
    return {
        "result": "held" if issues else "accepted",
        "issues": [{"path": i.path, "code": i.code, "text": i.text} for i in issues],
    }
