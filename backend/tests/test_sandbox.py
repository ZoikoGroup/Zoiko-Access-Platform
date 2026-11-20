"""A partner developer's sandbox: synthetic patients and a message checker (DEV-329)."""

import json

import pytest

from foundation import fhir, synthetic
from tests.test_exchange import Partner, body

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def test_every_synthetic_patient_is_plainly_synthetic_and_its_messages_behave_as_promised():
    rows = synthetic.sandbox()
    assert len(rows) == 5
    for r in rows:
        assert r["id"].startswith("synthetic-") and r["name"].startswith("Synthetic ")
        assert r["birth_date"][:4] in ("1900", "2099")
        assert fhir.validate(r["order"], profile=fhir.UK_CORE) == []
        assert r["name"] not in json.dumps(r["order"]) + json.dumps(r["report"])  # names nobody
        assert r["expect"] == ("held" if r["scenario"] == "held" else "accepted")
    assert {r["scenario"] for r in rows} == {"normal", "abnormal", "critical", "corrected", "held"}


def test_a_partner_developer_gets_the_sandbox_and_nobody_else_does():
    dev = Partner("R10", "dev1")
    r = dev.call("get", "portal/sandbox")
    assert r.status_code == 200 and len(body(r)["patients"]) == 5
    lab = Partner("R05", "lab9")
    assert lab.call("get", "portal/sandbox").status_code == 403


def test_the_checker_says_accepted_or_held_and_why():
    dev = Partner("R10", "dev2")
    good = synthetic.example_report(synthetic.PATIENTS[0])
    assert body(dev.call("post", "portal/sandbox/check", good)) == {"result": "accepted",
                                                                     "issues": []}
    bad = {**good, "code": {"coding": [{"system": "http://loinc.org", "code": "57021-8"}]}}
    got = body(dev.call("post", "portal/sandbox/check", bad))
    assert got["result"] == "held" and got["issues"]
    huge = {**good, "conclusion": "x" * 70_000}
    assert dev.call("post", "portal/sandbox/check", huge).status_code == 422


def test_the_checker_reads_an_hl7_v2_result_too():
    from tests.test_hl7 import oru

    dev = Partner("R10", "dev3")
    raw = oru(placer="synthetic-001-order")
    r = dev.client.post("/api/v1/portal/sandbox/check", raw, content_type="text/plain",
                        HTTP_AUTHORIZATION=f"Bearer dev:{dev.tenant_public_id}:idp:dev3")
    assert r.status_code == 200 and body(r)["result"] == "accepted", r.content
    bad = dev.client.post("/api/v1/portal/sandbox/check", "MSH|^~\&|A|B|C|D|1||ADT^A01|1|P|2.5",
                          content_type="text/plain",
                          HTTP_AUTHORIZATION=f"Bearer dev:{dev.tenant_public_id}:idp:dev3")
    assert body(bad)["result"] == "held" and "ORU" in body(bad)["issues"][0]["text"]
