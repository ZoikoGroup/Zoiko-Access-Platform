"""HL7 v2 lab results (ORU^R01) read into the FHIR the intake already takes (DEV-340)."""
import pytest

from foundation import fhir
from modules.integration.hl7 import Hl7Error, to_fhir


def oru(placer="abc_def", obr4="26604007^Full blood count^SCT", status="F", obx=None, nte=None):
    lines = [
        "MSH|^~\\&|LABSYS|LAB|ZOIKO|CLINIC|20261120093000||ORU^R01|MSG0001|P|2.5.1",
        "PID|1||999999^^^NHS^NH||Testperson^Real||19800101|F",  # must be ignored entirely
        f"ORC|RE|{placer}",
        f"OBR|1|{placer}|LAB-REP-77|{obr4}|||20261120080000|||||||||||||||20261120090000||HM|{status}",
    ]
    lines += obx if obx is not None else [
        "OBX|1|NM|26604007^Haemoglobin^SCT||128|g/L|115-165|N|||F",
        "OBX|2|NM|26604007^Potassium^SCT||6.8|mmol/L|3.5-5.3|HH|||F",
        "OBX|3|ST|26604007^Appearance^SCT||Haemolysed||||||F",
    ]
    lines += nte or ["NTE|1||Potassium very high; repeat advised."]
    return "\r".join(lines)


def test_an_oru_becomes_a_valid_uk_core_bundle_naming_nobody():
    b = to_fhir(oru())
    assert fhir.validate(b, profile=fhir.UK_CORE) == []
    report = fhir.report_of(b)
    assert report["basedOn"] == [{"reference": "ServiceRequest/abc.def"}]  # our id, FHIR-safe
    assert report["subject"] == {"reference": "Patient/abc.def"}  # a pseudonym, never PID
    assert report["conclusion"] == "Potassium very high; repeat advised."
    assert report["conclusionCode"][0]["coding"][0]["code"] == "CRIT"  # the worst value
    text = str(b)
    assert "Testperson" not in text and "999999" not in text and "1980" not in text
    hb, k, look = fhir.values_of(b)
    assert (hb["value"], hb["unit"], hb["low"], hb["high"], hb["flag"]) == (128, "g/L", 115, 165,
                                                                            "normal")
    assert k["flag"] == "critical" and look["value"] == "Haemolysed"


def test_a_corrected_result_and_local_codes_come_through_as_they_are():
    b = to_fhir(oru(status="C", obr4="FBC^Full blood count^L",
                    obx=["OBX|1|NM|HB^Haemoglobin^L||131|g/L|115-165|N|||C"]))
    report = fhir.report_of(b)
    assert report["status"] == "corrected"
    assert report["code"]["coding"][0]["system"] == "urn:hl7v2:local:L"
    assert fhir.validate(b, profile=fhir.UK_CORE)  # no SNOMED CT: held unless mapped


def test_what_cannot_be_read_is_refused_clearly():
    for bad in ("not hl7", "MSH|^~\\&|A|B|C|D|2026||ADT^A01|1|P|2.5",
                "MSH|^~\\&|A|B|C|D|2026||ORU^R01|1|P|2.5"):
        with pytest.raises(Hl7Error):
            to_fhir(bad)
    with pytest.raises(Hl7Error):
        to_fhir(oru(placer=""))


def test_escaped_separators_are_read_back():
    b = to_fhir(oru(nte=["NTE|1||Range 3\\T\\5 \\F\\ see note"]))
    assert fhir.report_of(b)["conclusion"] == "Range 3&5 | see note"



def test_an_imaging_report_comes_through_as_imaging_with_no_images():
    text = oru().replace("||HM|F", "||RAD|F").replace(
        "OBX|3|ST|26604007^Appearance^SCT||Haemolysed||||||F", "")
    b = to_fhir(text)
    report = fhir.report_of(b)
    assert report["category"] == [fhir.IMAGING_REPORT_CATEGORY]
    assert all(e["resource"].get("category") in ([fhir.IMAGING_REPORT_CATEGORY],
                                                 [fhir.IMAGING_VALUE_CATEGORY])
               for e in b["entry"])
    assert fhir.validate(b, profile=fhir.UK_CORE) == []
