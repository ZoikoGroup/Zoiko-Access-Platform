"""The strict FHIR R4 checker for the first slice: ServiceRequest and DiagnosticReport (DEV-315)."""

import copy

import pytest

from foundation import fhir

REQUEST = {
    "resourceType": "ServiceRequest",
    "id": "req-1",
    "identifier": [{"system": "urn:clinic:orders", "value": "ORD-0001"}],
    "status": "active",
    "intent": "order",
    "priority": "routine",
    "code": {
        "coding": [{"system": "http://loinc.org", "code": "718-7", "display": "Hemoglobin"}],
        "text": "Full blood count",
    },
    "subject": {"reference": "Patient/p1"},
    "requester": {"reference": "Practitioner/d1"},
    "authoredOn": "2026-11-17T09:30:00Z",
}
REPORT = {
    "resourceType": "DiagnosticReport",
    "id": "rep-1",
    "identifier": [{"system": "urn:lab:results", "value": "RES-0042"}],
    "status": "final",
    "code": {"coding": [{"system": "http://loinc.org", "code": "58410-2"}]},
    "subject": {"reference": "Patient/p1"},
    "basedOn": [{"reference": "ServiceRequest/req-1"}],
    "issued": "2026-11-17T11:00:00+00:00",
    "performer": [{"reference": "Organization/lab1"}],
    "conclusion": "Haemoglobin low.",
    "conclusionCode": [{"coding": [{"system": "urn:zoiko:rating", "code": "critical"}]}],
}


def problems(resource):
    return [(i.path, i.code) for i in fhir.validate(resource)]


def test_a_good_request_and_a_good_report_are_accepted():
    assert fhir.validate(REQUEST) == [] and fhir.validate(REPORT) == []
    assert fhir.FHIR_VERSION == "4.0.1"


def test_required_elements_are_required():
    for name in ("status", "intent", "subject"):
        broken = {k: v for k, v in REQUEST.items() if k != name}
        assert (name, "required") in problems(broken)
    for name in ("status", "code", "subject", "basedOn"):
        broken = {k: v for k, v in REPORT.items() if k != name}
        assert (name, "required") in problems(broken)


@pytest.mark.parametrize(
    "path,value",
    [
        ("status", "maybe"),
        ("intent", "wish"),
        ("priority", "whenever"),
    ],
)
def test_values_outside_the_fixed_sets_are_refused(path, value):
    assert (path, "value") in problems({**REQUEST, path: value})


def test_a_report_status_outside_the_set_is_refused():
    assert ("status", "value") in problems({**REPORT, "status": "done"})


def test_personal_data_hiding_places_are_refused_outright():
    for name in ("contained", "text", "extension", "modifierExtension", "meta"):
        got = problems({**REQUEST, name: [{"anything": "Jane Doe 9434765919"}]})
        assert (name, "forbidden") in got, name


def test_an_element_this_slice_does_not_use_is_refused_not_ignored():
    assert ("note", "unknown_element") in problems({**REQUEST, "note": [{"text": "free text"}]})
    assert ("bodySite", "unknown_element") in problems({**REPORT, "bodySite": []})


@pytest.mark.parametrize(
    "ref",
    [
        "Patient/",
        "patient/p1",
        "https://evil.example/Patient/p1",
        "Patient/p1/_history/2",
        "Patient/has space",
        "Person/p1",
        "Patient/" + "a" * 65,
        "",
        None,
        5,
    ],
)
def test_a_reference_must_be_a_plain_relative_one(ref):
    assert ("subject", "format") in problems({**REQUEST, "subject": {"reference": ref}})


def test_a_reference_may_not_carry_a_display_name_or_anything_else():
    got = problems({**REQUEST, "subject": {"reference": "Patient/p1", "display": "Jane Doe"}})
    assert ("subject", "format") in got


def test_a_code_needs_both_a_system_and_a_code():
    for coding in (
        {"code": "718-7"},
        {"system": "http://loinc.org"},
        {"system": "", "code": "1"},
        {"system": "x", "code": " 718-7"},
    ):
        bad = {**REQUEST, "code": {"coding": [coding]}}
        assert any(code in ("required", "format") for _, code in problems(bad)), coding


def test_a_code_may_not_carry_extra_elements_or_long_text():
    bad = {**REQUEST, "code": {"coding": [{"system": "x", "code": "1", "userSelected": True}]}}
    assert ("code.coding[0]", "format") in problems(bad)
    assert ("code.text", "format") in problems({**REQUEST, "code": {"text": "x" * 201}})


def test_dates_must_be_complete_with_a_time_zone():
    for bad in ("2026-11-17", "2026-11-17T09:30:00", "yesterday", 5):
        assert ("authoredOn", "format") in problems({**REQUEST, "authoredOn": bad})
        assert ("issued", "format") in problems({**REPORT, "issued": bad})


def test_a_report_answers_exactly_one_service_request():
    assert ("basedOn", "format") in problems({**REPORT, "basedOn": []})
    two = [{"reference": "ServiceRequest/a"}, {"reference": "ServiceRequest/b"}]
    assert ("basedOn", "format") in problems({**REPORT, "basedOn": two})
    wrong = [{"reference": "Practitioner/d1"}]
    assert ("basedOn[0]", "value") in problems({**REPORT, "basedOn": wrong})


def test_conclusion_is_plain_text_of_limited_length():
    assert ("conclusion", "format") in problems({**REPORT, "conclusion": ""})
    assert ("conclusion", "format") in problems({**REPORT, "conclusion": "x" * 2001})
    assert ("conclusion", "format") in problems({**REPORT, "conclusion": "a\x00b"})
    assert fhir.validate({**REPORT, "conclusion": "x" * 2000}) == []


def test_identifiers_are_short_and_few():
    assert ("identifier", "format") in problems({**REQUEST, "identifier": []})
    assert ("identifier[0]", "format") in problems({**REQUEST, "identifier": [{"value": "x" * 65}]})
    six = [{"value": f"v{i}"} for i in range(6)]
    assert ("identifier", "format") in problems({**REQUEST, "identifier": six})


def test_the_wrong_kind_of_thing_is_refused():
    for bad in (None, [], "text", 5, {}, {"resourceType": "Patient"}, {"resourceType": "Bundle"}):
        assert fhir.validate(bad), bad


def test_huge_or_deeply_nested_input_is_refused_before_anything_else():
    huge = {**REQUEST, "identifier": [{"value": "x"}] * 3, "code": {"text": "y"}}
    huge["subject"] = {"reference": "Patient/p1"}
    big = copy.deepcopy(huge)
    big["id"] = "a" * (300 * 1024)
    assert problems(big) == [("", "size")]
    nested: dict = {"resourceType": "ServiceRequest"}
    cursor = nested
    for _ in range(12):
        cursor["deep"] = {}
        cursor = cursor["deep"]
    assert problems(nested) == [("", "size")]


def test_every_problem_is_reported_not_just_the_first():
    got = problems({"resourceType": "ServiceRequest", "status": "x", "extension": []})
    assert ("intent", "required") in got and ("subject", "required") in got
    assert ("status", "value") in got and ("extension", "forbidden") in got
