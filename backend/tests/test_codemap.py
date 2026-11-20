"""Partners' local codes mapped to SNOMED CT by two people, never guessed (DEV-322)."""

import json

import pytest

from foundation import fhir
from foundation.cell_context import tenant_transaction
from modules.codemap import api as codemap
from tests.test_api import API
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
LOCAL = "urn:lab1:codes"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def platform():
    world = Platform()
    world.sam = world.staff("sam", "R11")
    world.tess = world.staff("tess", "R11")
    world.iyer = world.staff("iyer", "R12")
    world.ravi = world.staff("ravi", "R12")
    return world


def seen(platform, *codes):
    with tenant_transaction(platform.ctx):
        return codemap.note_seen([{"system": s, "code": c, "display": d} for s, c, d in codes])


def get(platform, subject="sam"):
    return platform.client.get(
        API + "platform/codemaps", **platform.header(platform.tenant_public_id, subject)
    )


def step(platform, mapping_id, name, subject, **data):
    return platform.client.post(
        API + f"platform/codemaps/{mapping_id}/{name}",
        json.dumps(data),
        content_type="application/json",
        **platform.header(platform.tenant_public_id, subject),
    )


def body(r):
    return json.loads(r.content)


def first(platform):
    return body(get(platform))["mappings"][0]["id"]


def test_each_local_code_is_listed_once_and_snomed_codes_are_not(platform):
    new = seen(
        platform,
        (LOCAL, "FBC", "Full blood count"),
        (LOCAL, "FBC", "Full blood count"),
        ("http://snomed.info/sct", "26604007", ""),
        (LOCAL, "K", "Potassium"),
    )
    assert new == 2
    rows = body(get(platform))["mappings"]
    assert {(r["source_code"], r["seen_count"], r["state"]) for r in rows} == {
        ("FBC", 2, "unmapped"),
        ("K", 1, "unmapped"),
    }


def test_support_proposes_and_a_different_safety_officer_approves(platform):
    seen(platform, (LOCAL, "FBC", "Full blood count"))
    m = first(platform)
    assert step(platform, m, "propose", "sam", target_code="26604007").status_code == 200
    with tenant_transaction(platform.ctx):
        assert codemap.lookup(LOCAL, "FBC") is None  # proposed is not approved
    done = step(platform, m, "approve", "iyer")
    assert done.status_code == 200 and body(done)["mapping"]["state"] == "approved"
    with tenant_transaction(platform.ctx):
        assert codemap.lookup(LOCAL, "FBC") == "26604007"


def test_only_support_proposes_and_only_a_safety_officer_decides(platform):
    seen(platform, (LOCAL, "FBC", ""))
    m = first(platform)
    assert step(platform, m, "propose", "iyer", target_code="26604007").status_code == 403
    assert step(platform, m, "propose", "sam", target_code="26604007").status_code == 200
    assert step(platform, m, "approve", "tess").status_code == 403  # support cannot approve
    assert step(platform, m, "approve", "ravi").status_code == 200


def test_nobody_decides_their_own_proposal(platform):
    """A login cannot hold both roles usefully (the policy engine answers under one purpose), so
    the rule is also enforced where the decision is stored."""
    from foundation.errors import ZoikoError

    seen(platform, (LOCAL, "FBC", ""))
    m = first(platform)
    with tenant_transaction(platform.ctx):
        codemap.propose(mapping_ref=m, target_code="26604007", by="same-person-01")
        for decide in (
            lambda: codemap.approve(mapping_ref=m, by="same-person-01"),
            lambda: codemap.reject(mapping_ref=m, reason="wrong_match", by="same-person-01"),
        ):
            with pytest.raises(ZoikoError) as refused:
                decide()
            assert refused.value.code.value == "POLICY_DENIED"


def test_a_proposal_must_be_a_real_looking_snomed_concept(platform):
    seen(platform, (LOCAL, "FBC", ""))
    m = first(platform)
    for bad in ("26604008", "abc", "", "026604007", None):
        assert step(platform, m, "propose", "sam", target_code=bad).status_code == 422


def test_rejecting_sends_it_back_for_a_new_proposal_and_reasons_are_fixed(platform):
    seen(platform, (LOCAL, "FBC", ""))
    m = first(platform)
    step(platform, m, "propose", "sam", target_code="26604007")
    assert step(platform, m, "reject", "iyer", reason="because").status_code == 422
    done = step(platform, m, "reject", "iyer", reason="wrong_match")
    assert body(done)["mapping"]["state"] == "rejected"
    assert step(platform, m, "propose", "tess", target_code="104866001").status_code == 200


def test_a_code_with_no_equivalent_can_be_rejected_without_a_proposal(platform):
    seen(platform, (LOCAL, "ZZ", ""))
    done = step(platform, first(platform), "reject", "iyer", reason="no_equivalent")
    assert done.status_code == 200 and body(done)["mapping"]["reject_reason"] == "no_equivalent"


def test_the_review_list_is_for_zoiko_staff_only(platform):
    from tests.test_clinician import Practice

    clinic = Practice()
    for who in ("ops", "doc", "pat"):
        assert clinic.get("platform/codemaps", subject=who).status_code == 403


def test_a_taken_in_message_lists_its_local_codes_and_a_refused_one_lists_nothing(platform):
    from api import views

    report = {
        "resourceType": "DiagnosticReport",
        "category": [fhir.LAB_REPORT_CATEGORY],
        "code": {
            "coding": [
                {"system": "http://snomed.info/sct", "code": "26604007"},
                {"system": LOCAL, "code": "FBC", "display": "Full blood count"},
            ]
        },
        "conclusionCode": [{"coding": [{"system": LOCAL, "code": "LOW"}]}],
    }
    views._note_codes(json.dumps(report).encode(), 202)
    views._note_codes(json.dumps(report).encode(), 401)  # refused: nothing is listed
    views._note_codes(b"not json", 202)  # never breaks the intake
    codes = sorted((r["source_code"], r["seen_count"]) for r in body(get(platform))["mappings"])
    assert codes == [("FBC", 1), ("LOW", 1)]
