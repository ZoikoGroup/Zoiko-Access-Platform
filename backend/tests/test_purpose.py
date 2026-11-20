"""A person states why they are acting; the engine accepts it only if their role may (GHE-06 "why",
purpose limitation). DEV-111."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.policy.models import AccessDecision
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

CONTINUITY = {"HTTP_X_ZOIKO_PURPOSE": "CONTINUITY_OF_CARE"}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    practice.add_times(practice.doc)
    booking = book(practice, practice.slot_of("doc"), subject="pat").json()["booking"]
    practice.post(f"bookings/{booking['id']}/confirm", subject="ops")
    return practice


def purposes_logged(p, actor):
    with tenant_transaction(p.clinic.ctx):
        return set(AccessDecision.objects.filter(actor_ref=actor).values_list(
            "purpose_code", flat=True))


def test_a_doctor_with_nothing_stated_acts_for_direct_care(p):
    assert p.get("clinician/patients", subject="doc").status_code == 200
    assert purposes_logged(p, p.doc) == {"DIRECT_CARE"}


def test_a_stated_purpose_is_what_gets_recorded(p):
    r = p.get("clinician/patients", subject="doc", **CONTINUITY)
    assert r.status_code == 200
    assert "CONTINUITY_OF_CARE" in purposes_logged(p, p.doc)


def listed(response, key):
    assert response.status_code == 200, response.content
    return json.loads(response.content)[key]


def test_following_up_earlier_care_is_read_only():
    from tests.test_referral import Clinic2
    c = Clinic2()
    body = {"patient": c.person, "service": c.cardiology, "reason": "Chest pain"}
    assert c.post("clinician/referrals", body, subject="doc", **CONTINUITY).status_code == 403
    assert c.post("clinician/referrals", body, subject="doc").status_code == 201   # direct care


def test_a_purpose_the_role_may_not_use_shows_nothing(p):
    assert len(listed(p.get("clinician/patients", subject="doc"), "patients")) == 1
    for wrong in ("OPERATIONS", "PAYMENT_COVERAGE"):
        got = p.get("clinician/patients", subject="doc", HTTP_X_ZOIKO_PURPOSE=wrong)
        assert listed(got, "patients") == [], wrong
    with tenant_transaction(p.clinic.ctx):
        assert AccessDecision.objects.filter(
            actor_ref=p.doc, purpose_code="OPERATIONS", allowed=False,
            reason_code="PURPOSE_NOT_ALLOWED").exists()


def test_a_patient_cannot_claim_a_clinical_purpose(p):
    assert listed(p.get("bookings", subject="pat"), "bookings")            # normal use works
    got = p.get("bookings", subject="pat", HTTP_X_ZOIKO_PURPOSE="DIRECT_CARE")
    assert listed(got, "bookings") == []


def test_staff_cannot_claim_direct_care(p):
    assert listed(p.get("bookings", subject="ops"), "bookings")
    got = p.get("bookings", subject="ops", HTTP_X_ZOIKO_PURPOSE="DIRECT_CARE")
    assert listed(got, "bookings") == []


def test_a_malformed_purpose_is_ignored_not_trusted(p):
    r = p.get("clinician/patients", subject="doc", HTTP_X_ZOIKO_PURPOSE="direct care; DROP")
    assert len(listed(r, "patients")) == 1
    assert purposes_logged(p, p.doc) == {"DIRECT_CARE"}
