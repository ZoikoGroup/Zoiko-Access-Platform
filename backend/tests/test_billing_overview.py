"""Staff "Billing & Claims": a read-only summary of insurance and who needs chasing before a visit.
It must never reveal what a patient has not chosen to share."""

import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust
from tests.test_api import book
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    practice = Practice()
    with tenant_transaction(practice.clinic.ctx):
        trust.link_person_to_site(practice.person, practice.clinic.org, "booking")
    return practice


def add(p, subject="pat", **over):
    body = {"insurer": "Star Health", "plan": "Family", "member_id": "STAR-12345-6789", **over}
    r = p.post("coverage", body, subject=subject)
    assert r.status_code == 201, r.content
    return r


def share(p, on=True):
    assert p.post("coverage/sharing", {"share": on}, subject="pat").status_code == 200


def view(p, subject="ops"):
    r = p.get("staff/billing", subject=subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def mine(body, person):
    return next(x for x in body["patients"] if x["id"] == person)


def visit(p):
    """The patient books a visit that is still to come at this clinic."""
    assert p.add_times(p.doc, first="2027-09-01T09:00:00", count=1).status_code == 201
    assert book(p, p.open_slots()[0]["id"]).status_code == 201


def in_days(n):
    return (dt.date.today() + dt.timedelta(days=n)).isoformat()


# ------------------------------------------------------------------ what it shows
def test_a_patient_with_shared_cover_in_force_is_covered(p):
    add(p, valid_until=in_days(400))
    share(p)
    body = view(p)
    row = mine(body, p.person)
    assert (row["state"], row["insurer"], row["plan"]) == ("covered", "Star Health", "Family")
    assert body["counts"]["covered"] == 1 and body["counts"]["needs_attention"] == 0


def test_cover_that_ends_soon_is_flagged_and_one_that_has_run_out_is_not_in_force(p):
    add(p, valid_until=in_days(10))
    share(p)
    assert mine(view(p), p.person)["state"] == "ending_soon"
    add(p, insurer="Old Insurer", valid_from="2020-01-01", valid_until=in_days(-1))
    assert mine(view(p), p.person)["insurer"] == "Star Health"    # the cover in force is shown


def test_a_patient_who_shared_but_has_no_cover_in_force_says_so(p):
    add(p, valid_from="2020-01-01", valid_until=in_days(-5))
    share(p)
    row = mine(view(p), p.person)
    assert row["state"] == "none_in_force" and row["insurer"] == ""


def test_no_end_date_counts_as_cover_that_does_not_end(p):
    add(p)
    share(p)
    assert mine(view(p), p.person)["state"] == "covered"


# ------------------------------------------------------------------ what it must not reveal
def test_a_patient_who_has_not_shared_reveals_nothing_not_even_that_they_have_insurance(p):
    add(p)                                           # they hold insurance but have not shared it
    body = view(p)
    row = mine(body, p.person)
    assert row["state"] == "not_shared"
    hidden = (row["insurer"], row["plan"], row["valid_until"], row["member_last4"])
    assert hidden == ("", "", None, "")
    assert "Star" not in json.dumps(body) and "6789" not in json.dumps(body)
    assert body["counts"]["not_shared"] == 1 and body["counts"]["covered"] == 0


def test_a_patient_with_no_insurance_and_one_who_has_not_shared_look_the_same(p):
    without = json.dumps(mine(view(p), p.person), sort_keys=True)
    add(p)
    with_hidden = json.dumps(mine(view(p), p.person), sort_keys=True)
    assert without == with_hidden


def test_unsharing_hides_the_details_again(p):
    add(p)
    share(p, True)
    assert mine(view(p), p.person)["insurer"] == "Star Health"
    share(p, False)
    assert mine(view(p), p.person)["insurer"] == ""


def test_the_member_number_is_never_returned_in_full(p):
    add(p)
    share(p)
    assert "STAR-12345-6789" not in json.dumps(view(p))


# ------------------------------------------------------------------ who to chase
def test_someone_with_a_visit_coming_and_no_cover_in_force_needs_attention(p):
    add(p, valid_from="2020-01-01", valid_until=in_days(-5))
    share(p)
    visit(p)
    body = view(p)
    assert [x["id"] for x in body["attention"]] == [p.person]
    assert body["counts"]["needs_attention"] == 1


def test_a_patient_who_has_not_shared_and_has_a_visit_coming_is_listed_to_ask(p):
    visit(p)
    body = view(p)
    assert [x["id"] for x in body["attention"]] == [p.person]
    assert mine(body, p.person)["state"] == "not_shared"


def test_covered_patients_and_patients_with_no_visit_are_not_listed(p):
    add(p, valid_until=in_days(400))
    share(p)
    visit(p)
    assert view(p)["attention"] == []                       # covered, visit coming
    add(p, valid_from="2020-01-01", valid_until=in_days(-5))
    p.post("coverage/sharing", {"share": False}, subject="pat")
    assert mine(view(p), p.person)["upcoming_visit"] is True


def test_a_cancelled_visit_does_not_count(p):
    visit(p)
    booking = json.loads(p.get("bookings", subject="pat").content)["bookings"][0]["id"]
    assert p.post(f"bookings/{booking}/cancel", {}, subject="pat").status_code in (200, 201)
    row = mine(view(p), p.person)
    assert row["upcoming_visit"] is False
    assert view(p)["attention"] == []


# ------------------------------------------------------------------ who may look
def test_a_patient_cannot_open_the_billing_summary(p):
    add(p)
    r = p.get("staff/billing", subject="pat")
    assert r.status_code == 403
    assert "Star" not in r.content.decode()


def test_another_clinics_staff_see_none_of_this_clinics_patients(p):
    add(p)
    share(p)
    with tenant_transaction(p.clinic.ctx):
        other_org = trust.create_organization(name="Other Clinic").public_id
        rival = trust.register_actor(
            actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="rival")
        trust.assign_role(actor_public_id=rival.public_id, organization_public_id=other_org,
                          role_code="R04", by="system")
    r = p.get("staff/billing", subject="rival")
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        body = json.loads(r.content)
        assert body["patients"] == [] and body["counts"]["patients"] == 0
        assert "Star Health" not in r.content.decode()


def test_the_summary_needs_a_login(p):
    assert p.client.get("/api/v1/staff/billing").status_code == 401
    assert p.client.post("/api/v1/staff/billing", {}, content_type="application/json",
                         **p.header("ops")).status_code == 405
