"""HL7 v2 lab results (ORU^R01) turned into the FHIR Bundle the intake already takes (DEV-340).

Many lab systems still send HL7 v2. Rather than a second path with its own rules, a message is
translated into exactly the FHIR shape we accept (a DiagnosticReport and its Observations,
DEV-330), and then goes through the same checks: UK Core, approved code mappings, matching the
order, and held for a person when anything is wrong. Nothing about the patient is taken from the
message: the PID segment (names, numbers, birth dates) is ignored, and the report is about a
per-order pseudonym, as every FHIR message is. The original text is what gets stored if held.

Read: MSH (separators, message type), ORC/OBR (OBR-2 or ORC-2 placer order number = our request
id; OBR-4 what was tested; OBR-25 result status), OBX (OBX-2 value type NM or ST, OBX-3 the test,
OBX-5 value, OBX-6 unit, OBX-7 range "low-high", OBX-8 flag, OBX-11 status), NTE (the comment
after OBR becomes the report's conclusion).
"""

from __future__ import annotations

import re
from typing import Any

from foundation import fhir

CODE_SYSTEMS = {
    "SCT": fhir.SNOMED, "SNM": fhir.SNOMED, "SNOMEDCT": fhir.SNOMED,
    "LN": "http://loinc.org", "LNC": "http://loinc.org",
}
# HL7 table 0078 abnormal flags -> the FHIR interpretation codes the intake reads.
FLAGS = {"N": "N", "A": "A", "H": "H", "L": "L", "HH": "HH", "LL": "LL", "AA": "AA",
         ">": "HH", "<": "LL"}
# OBR-25 / OBX-11 result status -> the FHIR status.
STATUS = {"F": "final", "C": "corrected", "A": "amended", "P": "preliminary", "X": "cancelled",
          "W": "entered-in-error", "R": "preliminary", "I": "registered"}
# HL7 table 0074 sections that are imaging (radiology, CT, MRI, ultrasound, nuclear medicine...).
IMAGING_SECTIONS = {"RAD", "RX", "CT", "CUS", "NMR", "NMS", "RUS", "VUS", "OUS", "XRC", "MR",
                    "US", "NM", "MG"}
_RANGE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")
_ID = re.compile(r"[^A-Za-z0-9\-.]")


class Hl7Error(ValueError):
    """The text is not an ORU^R01 message this intake can read."""


def _unescape(text: str, enc: str) -> str:
    if len(enc) < 3:
        return text
    comp, rep, esc = enc[0], enc[1], enc[2]
    sub = enc[3] if len(enc) > 3 else "&"
    for code, char in (("F", "|"), ("S", comp), ("R", rep), ("T", sub), ("E", esc)):
        text = text.replace(f"{esc}{code}{esc}", char)
    return text


def parse(text: str) -> list[list[str]]:
    """The segments as lists of fields (field 0 is the segment name). MSH is shifted so MSH-1 is
    the field separator, as HL7 numbers it."""
    if not isinstance(text, str) or not text.startswith("MSH") or len(text) < 8:
        raise Hl7Error("not an HL7 v2 message")
    sep = text[3]
    segments = [s for s in re.split(r"\r\n|\r|\n", text) if s.strip()]
    out = []
    for s in segments:
        fields = s.split(sep)
        if fields[0] == "MSH":
            fields = ["MSH", sep, *fields[1:]]
        out.append(fields)
    return out


def _field(seg: list[str], n: int) -> str:
    return seg[n] if len(seg) > n else ""


def _concept(raw: str, enc: str) -> dict[str, Any] | None:
    """CE/CWE "code^text^system" -> a FHIR CodeableConcept, or None when there is no code."""
    parts = [_unescape(p, enc) for p in raw.split(enc[0])] if raw else []
    code = parts[0].strip() if parts else ""
    if not code:
        return None
    name = parts[1].strip() if len(parts) > 1 else ""
    system_id = parts[2].strip().upper() if len(parts) > 2 else ""
    system = CODE_SYSTEMS.get(system_id) or f"urn:hl7v2:local:{system_id or 'none'}"
    concept: dict[str, Any] = {"coding": [{"system": system, "code": code}]}
    if name:
        concept["text"] = name[:200]
    return concept


def _number(text: str) -> float | int | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() and "." not in text else value


def to_fhir(text: str) -> dict[str, Any]:
    """An ORU^R01 message as the FHIR Bundle the intake takes. Raises Hl7Error when it cannot be
    read; anything it can read but the rules refuse is left to the same checks as FHIR."""
    segs = parse(text)
    msh = segs[0]
    enc = _field(msh, 2) or "^~\\&"
    message_type = _field(msh, 9).split(enc[0])
    if message_type[:2] != ["ORU", "R01"]:
        raise Hl7Error("only ORU^R01 lab results are taken")
    obr = next((s for s in segs if s[0] == "OBR"), None)
    orc = next((s for s in segs if s[0] == "ORC"), None)
    if obr is None:
        raise Hl7Error("no OBR segment")
    placer = (_field(obr, 2) or (_field(orc, 2) if orc else "")).split(enc[0])[0].strip()
    if not placer:
        raise Hl7Error("no placer order number (our request id) in OBR-2 or ORC-2")
    request_fhir_id = fhir.to_fhir_id(placer)
    subject = {"reference": f"Patient/{request_fhir_id}"}
    report_id = _ID.sub("-", (_field(obr, 3).split(enc[0])[0].strip() or f"{placer}-report"))[:64]
    # OBR-24 diagnostic service section: RAD (and its kin) is imaging (DEV-348); else laboratory.
    imaging = _field(obr, 24).split(enc[0])[0].strip().upper() in IMAGING_SECTIONS
    report: dict[str, Any] = {
        "resourceType": "DiagnosticReport",
        "id": report_id,
        "category": [fhir.IMAGING_REPORT_CATEGORY if imaging else fhir.LAB_REPORT_CATEGORY],
        "status": STATUS.get(_field(obr, 25).strip().upper(), "final"),
        "subject": subject,
        "basedOn": [{"reference": f"ServiceRequest/{request_fhir_id}"}],
    }
    code = _concept(_field(obr, 4), enc)
    if code:
        report["code"] = code
    after_obr = segs[segs.index(obr) + 1:]
    notes = [_unescape(_field(s, 3), enc).strip() for s in after_obr if s[0] == "NTE"]
    notes = [n for n in notes if n]
    if notes:
        report["conclusion"] = " ".join(notes)[:2000]
    observations = []
    worst = 0  # the report's overall rating: 0 normal, 1 abnormal, 2 critical
    forms = []
    plain = []
    for obx in (s for s in after_obr if s[0] == "OBX"):
        if _field(obx, 2).strip().upper() == "ED":
            # ED = source^type^subtype^encoding^data: a PDF report as base64 (DEV-349)
            ed_parts = _field(obx, 5).split(enc[0])
            if len(ed_parts) >= 5 and ed_parts[2].upper() == "PDF" and \
                    ed_parts[3].upper() == "BASE64":
                test_parts = _field(obx, 3).split(enc[0])
                title = _unescape(test_parts[1] if len(test_parts) > 1 else "", enc).strip()
                title = title or "report"
                forms.append({"contentType": "application/pdf", "data": ed_parts[4].strip(),
                              "title": (title if title.lower().endswith(".pdf")
                                        else title + ".pdf")[:100]})
            continue
        plain.append(obx)
    if forms:
        report["presentedForm"] = forms
    for i, obx in enumerate(plain, start=1):
        obs: dict[str, Any] = {
            "resourceType": "Observation",
            "id": f"{report_id}-v{i}"[:64],
            "category": [fhir.IMAGING_VALUE_CATEGORY if imaging else fhir.LAB_VALUE_CATEGORY],
            "status": STATUS.get(_field(obx, 11).strip().upper(), "final"),
            "subject": subject,
        }
        test = _concept(_field(obx, 3), enc)
        if test:
            obs["code"] = test
        raw_value = _unescape(_field(obx, 5), enc).strip()
        kind = _field(obx, 2).strip().upper()
        number = _number(raw_value) if kind in ("NM", "SN") else None
        unit = _unescape(_field(obx, 6).split(enc[0])[0], enc).strip()
        if number is not None:
            obs["valueQuantity"] = {"value": number, **({"unit": unit[:40]} if unit else {})}
        elif raw_value:
            obs["valueString"] = raw_value[:80]
        rng = _RANGE.match(_field(obx, 7))
        if rng:
            low, high = _number(rng.group(1)), _number(rng.group(2))
            obs["referenceRange"] = [{"low": {"value": low}, "high": {"value": high}}]
        flag = FLAGS.get(_field(obx, 8).split(enc[1])[0].strip().upper())
        if flag:
            obs["interpretation"] = [{"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                "code": flag}]}]
            rank = 2 if flag in ("HH", "LL", "AA") else (0 if flag == "N" else 1)
            worst = max(worst, rank)
        observations.append(obs)
    report["conclusionCode"] = [{"coding": [{
        "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
        "code": ("N", "A", "CRIT")[worst]}]}]
    if not observations:
        return report
    report["result"] = [{"reference": f"Observation/{o['id']}"} for o in observations]
    return {"resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": report}, *[{"resource": o} for o in observations]]}
