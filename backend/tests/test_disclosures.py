"""The disclosure log: every time information about a patient went to ANOTHER organization (a
referral, a request to a partner, a claim to a payer), as one list the patient can read."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from tests import test_claims as claims
from tests import test_exchange as exchange
from tests.test_api import API
from tests.test_clinician import Practice
from tests.test_platform import Platform
from tests.test_referral import Clinic2, issued

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def world():
    """A clinic whose patient has been seen, a lab, and a payer."""
    Platform()
    clinic = Practice()
    with tenant_transaction(clinic.clinic.ctx):
        trust.ensure_care_relationship(
            actor_public_id=clinic.doc, person_public_id=clinic.person,
            basis=trust.CareBasis.CARE_TEAM, organization_ref=clinic.clinic.org, by="system")
        trust.link_person_to_site(clinic.person, clinic.clinic.org, "booking")
    return clinic, exchange.Partner("R05", "lab1"), claims.Payer()


def log(clinic, subject="pat"):
    r = clinic.get("disclosures", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["entries"]


def test_a_patient_nothing_was_sent_about_has_an_empty_log(world):
    clinic, _, _ = world
    assert log(clinic) == []


def test_a_claim_is_listed_with_the_payer_and_the_details_are_not_shared_until_they_agree(world):
    clinic, _, payer = world
    claim_id = claims.made(clinic, payer, consent=False)
    (entry,) = log(clinic)
    assert (entry["kind"], entry["id"], entry["to"]) == ("claim", claim_id, payer.tenant_name)
    assert entry["what"] == "A claim for Consultation" and entry["shared"] is False
    assert claims.agree(clinic, claim_id).status_code == 200
    assert log(clinic)[0]["shared"] is True


def test_declining_a_claim_leaves_the_entry_with_nothing_shared(world):
    clinic, _, payer = world
    claim_id = claims.made(clinic, payer, consent=False)
    assert claims.agree(clinic, claim_id, "decline").status_code == 200
    (entry,) = log(clinic)
    assert entry["shared"] is False and entry["state"] == "withdrawn"


def test_a_request_to_a_partner_is_listed_and_shared_only_while_the_patient_agrees(world):
    clinic, lab, _ = world
    exchange.give_profile(clinic)
    request_id = exchange.made(clinic, lab, consent=False)
    (entry,) = log(clinic)
    assert (entry["kind"], entry["to"]) == ("request", lab.tenant_name)
    assert entry["what"] == "A lab order: Full blood count" and entry["shared"] is False
    exchange.agree(clinic, request_id)
    assert log(clinic)[0]["shared"] is True
    exchange.agree(clinic, request_id, "withdraw")
    assert log(clinic)[0]["shared"] is False


def test_the_log_mixes_everything_newest_first_and_names_no_one_but_the_recipient(world):
    clinic, lab, payer = world
    exchange.give_profile(clinic)
    exchange.made(clinic, lab, consent=False)
    claims.made(clinic, payer, consent=False)
    entries = log(clinic)
    assert [e["kind"] for e in entries] == ["claim", "request"]
    assert entries == sorted(entries, key=lambda e: e["at"], reverse=True)
    blob = json.dumps(entries)
    assert "MEM-0001" not in blob and "ORD-0001" not in blob and clinic.person not in blob
    assert all(set(e) == {"id", "kind", "at", "to", "what", "shared", "state"} for e in entries)


def test_a_referral_is_listed_with_the_receiving_service():
    c = Clinic2()
    made = issued(c)
    entries = log(c)
    (entry,) = [e for e in entries if e["kind"] == "referral"]
    assert entry["id"] == made["id"] and entry["what"] == "A referral for Cardiology"
    assert entry["shared"] is True and entry["to"]


def test_another_patient_never_sees_this_patients_log(world):
    clinic, _, payer = world
    claims.made(clinic, payer, consent=False)
    clinic.patient("other")
    assert log(clinic, "other") == []


def test_staff_have_no_patient_record_of_their_own_so_see_nothing(world):
    clinic, _, payer = world
    claims.made(clinic, payer, consent=False)
    assert log(clinic, "ops") == []


def test_a_young_patient_sees_their_own_log_and_not_their_parents(world):
    clinic, _, payer = world
    from tests.test_family_access import child, years_ago

    _, kid, _ = child(clinic, years_ago(10))
    r = clinic.post("claims/sent", claims.fields(clinic, payer, patient=kid), subject="ops")
    assert r.status_code == 201, r.content
    assert [e["kind"] for e in log(clinic, "kid")] == ["claim"]
    claims.made(clinic, payer, consent=False)                      # a claim for the parent
    assert [e["kind"] for e in log(clinic, "kid")] == ["claim"]    # the child sees only theirs
    assert [e["kind"] for e in log(clinic, "pat")] == ["claim"]    # and the parent only their own


def test_it_needs_a_login_and_only_reads(world):
    clinic, _, _ = world
    assert clinic.client.get(API + "disclosures").status_code == 401
    assert clinic.post("disclosures", {}, subject="pat").status_code == 405
