"""Intended-purpose statements (GHE-08 AT01, AT05, AT07; DEV-184): what each capability is for,
proposed by Zoiko staff and approved by a different safety officer before it can go live."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from tests.test_packs import call
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

STATEMENT = "Lets patients find and book a visit at a clinic. It decides nothing about care."


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def world():
    w = Platform()
    w.staff("sam", "R11")
    w.staff("iyer", "R12")
    w.staff("noor", "R12")
    return w


def propose(w, capability="booking", who="sam", **over):
    body = {"capability": capability, "statement": STATEMENT, "classification": "administrative",
            "intended_users": "Patients and clinic reception staff", **over}
    return call(w, "POST", "platform/purposes", body, who)


def made(w, **kw):
    r = propose(w, **kw)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["purpose"]


def step(w, purpose, name, who="iyer", **body):
    return call(w, "POST", f"platform/purposes/{purpose['public_id']}/{name}", body, who)


def listing(w, who="sam"):
    r = call(w, "GET", "platform/purposes", who=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["purposes"]


def test_support_or_safety_proposes_and_it_starts_unapproved(world):
    one = made(world)
    assert one["status"] == "draft" and one["version"] == 1 and one["approved_by_ref"] == ""
    assert made(world, capability="messaging", who="iyer")["status"] == "draft"
    assert {p["capability"] for p in listing(world, "noor")} == {"booking", "messaging"}


@pytest.mark.parametrize("over", [
    {"capability": "teleportation"}, {"statement": "too short"}, {"classification": "unknown"},
    {"classification": ""}, {"intended_users": ""}, {"legal_manufacturer": "x" * 121}])
def test_a_proposal_must_be_complete_and_use_a_real_capability_and_a_chosen_class(world, over):
    assert propose(world, **over).status_code == 422


def test_there_is_no_default_classification(world):
    body = {"capability": "booking", "statement": STATEMENT,
            "intended_users": "Patients and reception staff"}
    assert call(world, "POST", "platform/purposes", body).status_code == 422


def test_only_a_safety_officer_who_did_not_write_it_approves(world):
    one = made(world)
    assert step(world, one, "approve", "sam").status_code == 403           # support cannot
    mine = made(world, capability="messaging", who="iyer")
    assert step(world, mine, "approve", "iyer").status_code == 403         # not your own
    assert step(world, mine, "approve", "noor").status_code == 200
    got = step(world, one, "approve", "iyer")
    assert got.status_code == 200 and json.loads(got.content)["purpose"]["status"] == "approved"
    assert step(world, one, "approve", "noor").status_code == 409          # only once


def test_a_regulated_capability_needs_a_named_legal_manufacturer_to_be_approved(world):
    bare = made(world, capability="booking", classification="regulated")
    assert step(world, bare, "approve").status_code == 422                  # AT05
    named = made(world, capability="booking", classification="regulated",
                 legal_manufacturer="Example Devices Ltd")
    assert step(world, named, "approve").status_code == 200


def test_a_new_version_replaces_the_old_one_once_approved(world):
    first = made(world)
    step(world, first, "approve")
    second = made(world)
    assert second["version"] == 2
    step(world, second, "approve", "noor")
    states = {p["version"]: p["status"] for p in listing(world)}
    assert states == {1: "superseded", 2: "approved"}


def test_withdrawing_needs_a_reason_and_only_a_safety_officer(world):
    one = made(world)
    step(world, one, "approve")
    assert step(world, one, "retire", "sam", reason="No longer true").status_code == 403
    assert step(world, one, "retire", reason="").status_code == 422
    assert step(world, one, "retire", reason="Scope changed").status_code == 200
    assert step(world, one, "retire", reason="Again").status_code == 409
    assert step(world, one, "explode").status_code == 404


def test_every_step_is_audited(world):
    one = made(world)
    step(world, one, "approve")
    step(world, one, "retire", reason="Scope changed")
    with tenant_transaction(world.ctx):
        actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"safety.purpose_proposed", "safety.purpose_approved",
            "safety.purpose_retired"} <= actions


# ------------------------------------------------------- AT02: a change of class is reviewed twice
def approved_first(w, capability="booking"):
    one = made(w, capability=capability)
    assert step(w, one, "approve").status_code == 200
    return one


def test_a_change_of_class_needs_two_different_safety_officers(world):
    approved_first(world)                                             # administrative
    changed = made(world, classification="regulated", legal_manufacturer="Example Devices Ltd")
    assert changed["reclassifies"] is True and changed["reviews_needed"] == 2
    first = json.loads(step(world, changed, "approve", "noor").content)["purpose"]
    assert first["status"] == "draft" and first["reviews"] == 1       # one review: still a proposal
    assert step(world, changed, "approve", "noor").status_code == 403  # not the same person twice
    last = json.loads(step(world, changed, "approve", "iyer").content)["purpose"]
    assert last["status"] == "approved"
    assert {p["version"]: p["status"] for p in listing(world)} == {1: "superseded", 2: "approved"}


def test_the_author_is_neither_reviewer_of_a_change_of_class(world):
    world.staff("rao", "R12")
    approved_first(world)
    mine = made(world, who="iyer", classification="clinical_support")
    assert mine["reclassifies"] is True
    assert step(world, mine, "approve", "iyer").status_code == 403    # not as first reviewer
    assert step(world, mine, "approve", "noor").status_code == 200    # first review
    assert step(world, mine, "approve", "iyer").status_code == 403    # not as second either
    done = json.loads(step(world, mine, "approve", "rao").content)["purpose"]
    assert done["status"] == "approved"


def test_only_a_changed_class_is_flagged_never_the_first_statement_or_the_same_class(world):
    first = made(world)
    assert first["reclassifies"] is False and first["reviews_needed"] == 1
    step(world, first, "approve")
    same = made(world)                                                # same class, new wording
    assert same["reclassifies"] is False
    done = json.loads(step(world, same, "approve", "noor").content)["purpose"]
    assert done["status"] == "approved"


def test_the_first_review_is_audited(world):
    approved_first(world)
    changed = made(world, classification="clinical_support")
    step(world, changed, "approve", "noor")
    with tenant_transaction(world.ctx):
        assert "safety.purpose_first_review" in set(
            AuditEvent.objects.values_list("action", flat=True))
