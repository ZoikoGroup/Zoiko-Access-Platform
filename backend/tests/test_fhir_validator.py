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
    "category": [fhir.LAB_REPORT_CATEGORY],
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


# ------------------------------------------------------------------ UK Core (DEV-319)
UK_REQUEST = {
    **REQUEST,
    "code": {
        "coding": [
            {"system": "http://snomed.info/sct", "code": "26604007", "display": "Full blood count"}
        ]
    },
}
UK_REPORT = {
    **REPORT,
    "code": {"coding": [{"system": "http://snomed.info/sct", "code": "26604007"}]},
    "conclusionCode": [{"coding": [{"system": "http://snomed.info/sct", "code": "271737000"}]}],
}


def uk(resource):
    return [(i.path, i.code) for i in fhir.validate(resource, profile=fhir.UK_CORE)]


def test_uk_core_accepts_resources_coded_in_snomed_ct():
    assert uk(UK_REQUEST) == [] and uk(UK_REPORT) == []
    assert fhir.validate(UK_REQUEST) == []  # the base check still passes too


def test_uk_core_needs_a_snomed_ct_code():
    loinc_only = {**REQUEST}  # LOINC only: fine in base FHIR
    assert fhir.validate(loinc_only) == []
    assert ("code", "required") in uk(loinc_only)


@pytest.mark.parametrize(
    "code", ["26604008", "1234567", "026604007", "12345", "abc", "", "26604017"]
)
def test_a_snomed_code_must_be_a_real_looking_concept_identifier(code):
    bad = {**UK_REQUEST, "code": {"coding": [{"system": "http://snomed.info/sct", "code": code}]}}
    got = uk(bad)
    assert ("code.coding[0].code", "format") in got or ("code.coding[0].code", "required") in got


def test_the_snomed_check_digit_is_the_verhoeff_scheme():
    for good in ("26604007", "104866001", "365636006", "271737000", "71388002"):
        assert fhir.sctid_ok(good), good
    assert not fhir.verhoeff_ok("26604008")


def test_a_conclusion_code_in_snomed_ct_is_checked_too():
    bad = {
        **UK_REPORT,
        "conclusionCode": [{"coding": [{"system": "http://snomed.info/sct", "code": "271737001"}]}],
    }
    assert ("conclusionCode[0].coding[0].code", "format") in uk(bad)


def test_an_nhs_number_is_never_accepted_anywhere_in_a_uk_message():
    by_system = {
        **UK_REQUEST,
        "identifier": [{"system": "https://fhir.nhs.uk/Id/nhs-number", "value": "9434765919"}],
    }
    assert ("", "forbidden") in uk(by_system)
    in_text = {**UK_REPORT, "conclusion": "Result for 943 476 5919: low."}
    assert ("", "forbidden") in uk(in_text)


def test_an_unknown_profile_is_refused():
    assert [i.code for i in fhir.validate(UK_REQUEST, profile="us-core")] == ["value"]


# ------------------------------------------------------------------ the order we send (DEV-323)
def test_the_order_we_send_is_a_uk_core_service_request_naming_nobody():
    made = fhir.service_request(
        request_ref="req-abc123",
        order_no="ORD-0001",
        kind="lab_order",
        urgency="urgent",
        snomed_code="26604007",
        label="Full blood count",
        authored_on="2026-11-19T09:00:00Z",
    )
    assert fhir.validate(made, profile=fhir.UK_CORE) == []
    assert made["priority"] == "urgent" and made["subject"] == {"reference": "Patient/req-abc123"}
    text = str(made)
    assert "birth" not in text and "name" not in text


def test_an_order_without_a_real_snomed_code_is_never_built():
    for code in ("26604008", "", "abc"):
        with pytest.raises(ValueError):
            fhir.service_request(
                request_ref="req-abc123",
                order_no="ORD-0001",
                kind="lab_order",
                urgency="routine",
                snomed_code=code,
                label="Full blood count",
                authored_on="2026-11-19T09:00:00Z",
            )
    with pytest.raises(ValueError):
        fhir.service_request(
            request_ref="req-abc123",
            order_no="ORD-0001",
            kind="x-ray",
            urgency="routine",
            snomed_code="26604007",
            label="x",
            authored_on="2026-11-19T09:00:00Z",
        )


def _obs(oid, value, unit="g/L", low=115, high=165, flag="N", subject="Patient/p-1",
         code="26604007"):
    return {
        "resourceType": "Observation",
        "category": [fhir.LAB_VALUE_CATEGORY], "id": oid, "status": "final",
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": code}],
                 "text": "Haemoglobin"},
        "subject": {"reference": subject},
        "valueQuantity": {"value": value, "unit": unit},
        "referenceRange": [{"low": {"value": low}, "high": {"value": high}}],
        "interpretation": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            "code": flag}]}],
    }


def _bundle_of(report, *observations):
    report = {**report, "result": [{"reference": f"Observation/{o['id']}"} for o in observations]}
    return {"resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": report}, *[{"resource": o} for o in observations]]}


# ------------------------------------------------------------------ result values (DEV-330)
_REPORT = {
    "resourceType": "DiagnosticReport",
    "category": [fhir.LAB_REPORT_CATEGORY], "id": "rep-1", "status": "final",
    "code": {"coding": [{"system": "http://snomed.info/sct", "code": "26604007"}]},
    "subject": {"reference": "Patient/p-1"},
    "basedOn": [{"reference": "ServiceRequest/req-1"}],
}


def test_a_report_with_its_values_is_a_valid_bundle_and_the_values_come_out_plainly():
    b = _bundle_of(_REPORT, _obs("o1", 128), _obs("o2", 98, flag="L", low=100, high=200))
    assert fhir.validate(b, profile=fhir.UK_CORE) == []
    assert fhir.report_of(b)["id"] == "rep-1"
    first, second = fhir.values_of(b)
    assert first == {"name": "Haemoglobin", "code": "26604007", "value": 128, "unit": "g/L",
                     "low": 115, "high": 165, "flag": "normal"}
    assert second["flag"] == "low"


def test_a_bundle_must_hold_one_report_and_exactly_the_values_it_lists():
    o = _obs("o1", 128)
    unlisted = {"resourceType": "Bundle", "type": "collection",
                "entry": [{"resource": _REPORT}, {"resource": o}]}
    assert any(i.path == "result" for i in fhir.validate(unlisted))
    other_patient = _bundle_of(_REPORT, _obs("o1", 128, subject="Patient/p-2"))
    assert any(i.path == "entry" for i in fhir.validate(other_patient))
    two_reports = {"resourceType": "Bundle", "type": "collection",
                   "entry": [{"resource": _REPORT}, {"resource": _REPORT}]}
    assert fhir.validate(two_reports)
    assert fhir.validate({**_bundle_of(_REPORT, o), "type": "transaction"})


def test_a_value_is_a_number_or_a_short_text_and_nothing_else():
    bad = _obs("o1", "128")  # a quantity's value must be a number
    assert any(i.path.startswith("valueQuantity") for i in fhir.validate(bad))
    both = {**_obs("o1", 128), "valueString": "positive"}
    assert any(i.path == "value[x]" for i in fhir.validate(both))
    text = {k: v for k, v in _obs("o1", 1).items() if k != "valueQuantity"}
    assert fhir.validate({**text, "valueString": "Positive"}, profile=fhir.UK_CORE) == []
    assert fhir.validate({**text, "valueString": "x" * 81})
    assert fhir.validate({**_obs("o1", 1), "note": [{"text": "free text"}]})  # not accepted
    loinc = {**_obs("o1", 1), "code": {"coding": [{"system": "http://loinc.org", "code": "718-7"}]}}
    assert fhir.validate(loinc, profile=fhir.UK_CORE)  # UK Core needs SNOMED CT here too



# ------------------------------------------------------------------ ids (DEV-331)
def test_every_zoiko_id_becomes_a_valid_fhir_id_and_back():
    from foundation.ids import new_public_id

    ids = [new_public_id() for _ in range(300)] + ["ab_cd-ef_gh_ij-kl_mn_op", "__________"]
    assert any("_" in i for i in ids)
    for public in ids:
        made = fhir.service_request(
            request_ref=public, order_no="ORD-0001", kind="lab_order", urgency="routine",
            snomed_code="26604007", label="Full blood count", authored_on="2026-11-19T09:00:00Z")
        assert fhir.validate(made, profile=fhir.UK_CORE) == []
        assert fhir.from_fhir_id(made["id"]) == public


# ------------------------------------------------ UK Core lab profiles, release 2.0.2 (DEV-342)
def test_a_uk_core_lab_report_with_its_category_is_accepted_and_one_without_is_held():
    with_cat = {**_REPORT, "category": [fhir.LAB_REPORT_CATEGORY]}
    assert fhir.validate(with_cat, profile=fhir.UK_CORE) == []  # used to be "unknown element"
    without = {k: v for k, v in _REPORT.items() if k != "category"}
    assert fhir.validate(without) == []  # plain FHIR does not need it
    held = fhir.validate(without, profile=fhir.UK_CORE)
    assert [i.path for i in held] == ["category"] and "2.0.2" in held[0].text
    radiology = {**_REPORT, "category": [fhir.IMAGING_REPORT_CATEGORY]}  # DEV-348: imaging too
    assert fhir.validate(radiology, profile=fhir.UK_CORE) == []
    other = {**_REPORT, "category": [{"coding": [{
        "system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "PAT"}]}]}
    assert [i.path for i in fhir.validate(other, profile=fhir.UK_CORE)] == ["category"]


def test_each_lab_value_needs_the_laboratory_category_under_uk_core():
    value = _obs("o1", 128)
    assert fhir.validate(value, profile=fhir.UK_CORE) == []
    bare = {k: v for k, v in value.items() if k != "category"}
    assert [i.path for i in fhir.validate(bare, profile=fhir.UK_CORE)] == ["category"]
    bad = {**value, "category": "laboratory"}
    assert any(i.path == "category" for i in fhir.validate(bad))


# ------------------------------------------------ a PDF inside the report (DEV-349)
def test_a_report_may_carry_its_pdf_and_the_size_rule_measures_the_message_without_it():
    import base64

    pdf = b"%PDF-1.4\n" + b"%" + b"x" * (2 * 1024 * 1024) + b"\n%%EOF\n"
    form = {"contentType": "application/pdf", "title": "FBC.pdf",
            "data": base64.b64encode(pdf).decode()}
    report = {**_REPORT, "category": [fhir.LAB_REPORT_CATEGORY], "presentedForm": [form]}
    assert fhir.validate(report, profile=fhir.UK_CORE) == []  # 2 MB of PDF is fine
    assert fhir.pdf_forms(report) == [("FBC.pdf", pdf)]
    for bad in ({**form, "contentType": "image/png"}, {**form, "data": "not base64!"},
                {**form, "data": base64.b64encode(b"GIF89a").decode()}, {**form, "url": "x"}):
        assert fhir.validate({**report, "presentedForm": [bad]}), bad
    assert fhir.validate({**report, "presentedForm": [form] * 6})
