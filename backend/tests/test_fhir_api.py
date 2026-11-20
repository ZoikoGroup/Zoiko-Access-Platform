"""Signed FHIR intake and staff triage through the real HTTP routes (DEV-315, GHE-13 T-25, T-60).

The unit tests in test_fhir_intake.py cover the module in isolation; these check that a partner can
actually post a message, that a bad one creates no clinical state, and that only a clinic
administrator can decide what to do with it.
"""
import json
import time

import pytest
from django.test import Client

from modules.integration.delivery import signature_header
from modules.trust import api as trust
from tests.partner_support import issue
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


def partner(p):
    """A certified partner whose secret has been issued (T-43)."""
    r = p.post("staff/integrations/partners",
               {"label": "Lab network", "allowed_types": ["partner.lab."]}, subject="boss")
    assert r.status_code == 201, r.content
    return issue(p, json.loads(r.content)["client"])


def send_fhir(c, raw, delivery="fhir-delivery-0001"):
    stamp = int(time.time())
    header = signature_header(c["secret"], c["key_id"], stamp, delivery, raw)
    return Client().post(
        "/api/v1/fhir", raw, content_type="application/fhir+json",
        HTTP_ZOIKO_SIGNATURE=header, HTTP_ZOIKO_DELIVERY_ID=delivery,
    )


def quarantined(p, state="held", subject="boss"):
    r = p.get(f"staff/fhir/quarantine?state={state}", subject=subject)
    assert r.status_code == 200, r.content
    return r.json()["items"]


def test_an_invalid_resource_is_held_and_triaged_by_staff(p):
    c = partner(p)
    # Valid JSON, but the resource is missing the required intent and subject.
    raw = json.dumps({"resourceType": "ServiceRequest", "status": "active"}).encode()
    r = send_fhir(c, raw)
    assert r.status_code == 202, r.content
    assert r.json()["quarantined"] is True
    qid = r.json()["id"]

    [item] = quarantined(p)
    assert item["public_id"] == qid and item["resource_type"] == "ServiceRequest"
    assert {i["path"] for i in item["issues"]} >= {"intent", "subject"}

    r = p.post(f"staff/fhir/quarantine/{qid}/discard", {"note": "Not our partner"},
               subject="boss")
    assert r.status_code == 200, r.content
    assert r.json()["item"]["state"] == "discarded"
    assert quarantined(p) == []
    assert [i["public_id"] for i in quarantined(p, "discarded")] == [qid]

    # A decided message cannot be decided again.
    assert p.post(f"staff/fhir/quarantine/{qid}/discard", {}, subject="boss").status_code == 409


def test_only_a_clinic_administrator_can_read_or_triage_the_quarantine(p):
    c = partner(p)
    raw = json.dumps({"resourceType": "ServiceRequest", "status": "active"}).encode()
    send_fhir(c, raw)
    for who in ("ops", "doc", "pat"):
        assert p.get("staff/fhir/quarantine", subject=who).status_code == 403
    qid = quarantined(p)[0]["public_id"]
    assert p.post(f"staff/fhir/quarantine/{qid}/discard", {}, subject="ops").status_code == 403


def test_a_valid_service_request_is_acknowledged_without_quarantine(p):
    c = partner(p)
    raw = json.dumps({
        "resourceType": "ServiceRequest", "id": "sr-1", "status": "active",
        "intent": "order", "subject": {"reference": "Patient/abc"},
    }).encode()
    r = send_fhir(c, raw)
    assert r.status_code == 202, r.content
    assert r.json()["quarantined"] is False
    assert quarantined(p) == []


def test_the_same_delivery_twice_keeps_one_quarantine_row(p):
    c = partner(p)
    raw = json.dumps({"resourceType": "ServiceRequest", "status": "active"}).encode()
    first = send_fhir(c, raw)
    second = send_fhir(c, raw)
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["duplicate"] is True
    assert len(quarantined(p)) == 1
