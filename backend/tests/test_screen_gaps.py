"""Closing gaps between the screens and the product (DEV-353): the clinic dashboard counts its
claims, the partner overview says truthfully what is and is not available, and a doctor can open and
send files in a conversation."""
import base64
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.billing import payments
from modules.comms import api as comms
from modules.trust import api as trust
from tests import test_claims as claims
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PDF = b"%PDF-1.4\n%%EOF\n"
NONE = {"waiting": 0, "approved": 0, "paid": 0, "declined": 0}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    comms.register_subscribers()


def three_sides():
    """The platform, a clinic that has seen its patient, and a payer."""
    platform = Platform()
    clinic = Practice()
    with tenant_transaction(clinic.clinic.ctx):
        trust.link_person_to_site(clinic.person, clinic.clinic.org, "booking")
    return platform, clinic, claims.Payer()


def dashboard(clinic, subject="ops"):
    r = clinic.get("staff/dashboard", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


# ------------------------------------------------------------------ claims on the dashboard
def test_the_dashboard_counts_the_clinics_claims_by_where_they_stand():
    _, clinic, payer = three_sides()
    assert dashboard(clinic)["claims"] == NONE
    claim_id = claims.made(clinic, payer)
    assert dashboard(clinic)["claims"] == {**NONE, "waiting": 1}
    assert claims.step(payer, claim_id, "approve").status_code == 200
    assert dashboard(clinic)["claims"] == {**NONE, "approved": 1}
    assert claims.step(payer, claim_id, "pay", payment_ref="PAY-001").status_code == 200
    assert dashboard(clinic)["claims"] == {**NONE, "paid": 1}


def test_another_clinic_never_counts_these_claims():
    _, clinic, payer = three_sides()
    claims.made(clinic, payer)
    other = Practice()
    assert dashboard(other)["claims"] == NONE


def test_without_a_place_for_claims_the_dashboard_shows_none_of_them():
    assert dashboard(Practice())["claims"] is None


def test_patients_and_doctors_have_no_clinic_dashboard():
    clinic = Practice()
    for who in ("pat", "doc"):
        assert clinic.get("staff/dashboard", subject=who).status_code == 403, who
    assert clinic.client.get(API + "staff/dashboard").status_code == 401


# ------------------------------------------------------------------ what a partner is told
@pytest.mark.parametrize("role,login_kind,connected", [
    ("R05", trust.ActorType.FACILITY_USER, "Requests clinics send you"),
    ("R07", trust.ActorType.PAYER_USER, "Claims clinics send you"),
    ("R08", trust.ActorType.EMPLOYER_USER, "One-time codes"),
])
def test_the_overview_lists_what_now_works_and_does_not_call_it_missing(
        role, login_kind, connected):
    clinic = Practice()
    clinic.login("someone", login_kind, role)
    body = json.loads(clinic.get("portal/overview", subject="someone").content)
    assert any(connected in line for line in body["connected"])
    missing = " ".join(body["waiting"])
    assert missing and "separate customers" not in missing and "Claims, and" not in missing


# ------------------------------------------------------------------ files, for a doctor
@pytest.fixture
def cared():
    """A clinic where `doc` cares for the patient and `doc2` does not."""
    practice = Practice()
    with tenant_transaction(practice.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=practice.doc, person_public_id=practice.person,
            basis=trust.CareBasis.CARE_TEAM, organization_ref=practice.clinic.org, by="system")
    return practice


def attached(name="results.pdf"):
    return [{"name": name, "data": base64.b64encode(PDF).decode()}]


def started_with_a_file(p):
    r = p.post("messages/threads", {"subject": "My results", "body": "Attached.",
                                    "attachments": attached()}, subject="pat")
    assert r.status_code == 201, r.content
    return json.loads(r.content)["thread"]["id"]


def files_in(p, thread, subject):
    r = p.get(f"messages/threads/{thread}", subject=subject)
    assert r.status_code == 200, r.content
    return [a for m in json.loads(r.content)["thread"]["messages"] for a in m["attachments"]]


def test_the_treating_doctor_opens_a_patients_file_even_when_only_following_up(cared):
    thread = started_with_a_file(cared)
    (file,) = files_in(cared, thread, "doc")
    for extra in ({}, {"HTTP_X_ZOIKO_PURPOSE": "CONTINUITY_OF_CARE"}):
        got = cared.get(f"messages/attachments/{file['id']}", subject="doc", **extra)
        assert got.status_code == 200 and got.content == PDF


def test_a_doctor_who_does_not_care_for_the_patient_cannot_open_the_file(cared):
    thread = started_with_a_file(cared)
    (file,) = files_in(cared, thread, "doc")
    assert cared.get(f"messages/attachments/{file['id']}", subject="doc2").status_code == 403


def test_the_doctor_sends_a_file_and_the_patient_receives_it(cared):
    thread = started_with_a_file(cared)
    r = cared.post(f"messages/threads/{thread}/reply",
                   {"body": "Here is the leaflet.", "attachments": attached("leaflet.pdf")},
                   subject="doc")
    assert r.status_code == 200, r.content
    assert [a["name"] for a in files_in(cared, thread, "pat")] == ["results.pdf", "leaflet.pdf"]


def test_a_doctor_following_up_read_only_cannot_send_a_file(cared):
    thread = started_with_a_file(cared)
    r = cared.post(f"messages/threads/{thread}/reply",
                   {"body": "No.", "attachments": attached("no.pdf")}, subject="doc",
                   HTTP_X_ZOIKO_PURPOSE="CONTINUITY_OF_CARE")
    assert r.status_code == 403
    assert [a["name"] for a in files_in(cared, thread, "pat")] == ["results.pdf"]


# ------------------------------------------------------------------ paying online, as apps see it
def bills(clinic, subject="pat"):
    r = clinic.get("billing/invoices", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def test_the_bills_say_online_payment_is_off_unless_the_deployment_switches_it_on(settings):
    settings.BILLING_PAYMENT_PROVIDER = ""
    assert bills(Practice())["online_payment"] == {"available": False, "pretend": False}


def test_the_bills_say_when_payment_is_on_and_that_it_moves_no_money(settings):
    settings.BILLING_PAYMENT_PROVIDER = "pretend"
    payments.register(payments.pretend_provider)
    assert bills(Practice())["online_payment"] == {"available": True, "pretend": True}


def test_someone_without_their_own_patient_record_sees_no_bills_and_a_stranger_nothing(settings):
    settings.BILLING_PAYMENT_PROVIDER = ""
    clinic = Practice()
    assert bills(clinic, "ops")["invoices"] == []
    assert clinic.client.get(API + "billing/invoices").status_code == 401

