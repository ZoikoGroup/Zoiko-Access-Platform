"""A workflow definition can require country-pack capabilities, and then starts only where a signed,
current pack for the customer's country switches them on (GHE-12 WF-T01, WF-T03, DEV-156)."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.jurisdiction.models import CountryPack
from modules.trust import api as trust
from tests.test_clinician import Practice
from tests.test_country_deadline import set_country
from tests.test_orchestration import ORIGIN, PATH, ev, fetch, post
from tests.test_packs import full, make, signed, step, to_review
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.WORKFLOW_CLINICAL_ENABLED = False


@pytest.fixture
def world():
    w = Platform()
    for subject, role in (("sam", "R11"), ("tess", "R11"), ("iyer", "R12")):
        w.staff(subject, role)
    return w


@pytest.fixture
def p():
    practice = Practice()
    practice.boss_id = practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    return practice


def manifest_with(*capabilities, days=30):
    m = full(days=days)
    m["capabilities"] = {name: True for name in capabilities}
    return m


def define(p, needs, key="gated-flow", publish=True):
    body = {"key": key, "journey": "visit", "path": PATH, "timer_policies": []}
    if needs is not None:
        body["required_capabilities"] = needs
    r = post(p, "staff/workflows/definitions", body)
    assert r.status_code == 201, r.content
    made = json.loads(r.content)["definition"]
    if publish:
        r = post(p, f"staff/workflows/definitions/{made['public_id']}/publish", {})
        assert r.status_code == 200, r.content
        made = json.loads(r.content)["definition"]
    return made


def start(p, key="gated-flow", subject="subject-0001"):
    return post(p, "staff/workflows", {
        "definition": key, "subject": subject, "owner": p.desk, "evidence": ORIGIN,
        "purpose": "OPERATIONS"}, "ops")


# ------------------------------------------------------------------ the definition
@pytest.mark.parametrize("bad", ["booking", {"booking": True}, ["nonsense"], ["booking", "booking"],
                                 [1], ["booking"] * 20])
def test_only_known_capabilities_each_once_may_be_required(p, bad):
    r = post(p, "staff/workflows/definitions", {
        "key": "bad-flow", "journey": "visit", "path": PATH, "timer_policies": [],
        "required_capabilities": bad})
    assert r.status_code == 422


def test_a_definition_names_what_it_needs_and_that_changes_its_fingerprint(p):
    plain = define(p, None, key="plain-flow")
    gated = define(p, ["messaging", "booking"], key="other-flow")
    assert plain["required_capabilities"] == [] and gated["required_capabilities"] == [
        "booking", "messaging"]
    assert plain["content_hash"] != gated["content_hash"]


def test_a_draft_can_change_what_it_needs_and_a_new_version_keeps_it(p):
    made = define(p, ["booking"], publish=False)
    r = post(p, f"staff/workflows/definitions/{made['public_id']}/edit", {
        "path": PATH, "timer_policies": [], "required_capabilities": ["messaging"]})
    edited = json.loads(r.content)["definition"]
    assert edited["required_capabilities"] == ["messaging"]
    assert edited["content_hash"] != made["content_hash"]
    post(p, f"staff/workflows/definitions/{made['public_id']}/publish", {})
    r = post(p, f"staff/workflows/definitions/{made['public_id']}/version", {})
    assert json.loads(r.content)["definition"]["required_capabilities"] == ["messaging"]


# ------------------------------------------------------------------ WF-T01: when it may start
def test_a_definition_that_needs_nothing_starts_without_any_pack(p):
    define(p, None, key="plain-flow")
    assert start(p, key="plain-flow").status_code == 201


def test_without_a_country_or_a_pack_a_gated_workflow_is_refused(world, p):
    define(p, ["booking"])
    r = start(p)
    assert r.status_code == 403 and json.loads(r.content)["code"] == "JURISDICTION_INACTIVE"
    set_country(world, p, "GB")
    assert start(p).status_code == 403                                  # a country, no pack


def test_a_draft_a_pack_in_review_and_a_pack_for_another_country_do_not_count(world, p):
    define(p, ["booking"])
    set_country(world, p, "GB")
    make(world, ref="gb-just-a-draft", manifest=manifest_with("booking"))
    to_review(world, ref="gb-in-review", manifest=manifest_with("booking"))
    signed(world, ref="de-signed", country="DE", manifest=manifest_with("booking"))
    assert start(p).status_code == 403


def test_a_signed_pack_that_switches_the_capability_on_lets_it_start(world, p):
    define(p, ["booking"])
    signed(world, ref="gb-care-navigation", manifest=manifest_with("booking"))
    set_country(world, p, "GB")
    assert start(p).status_code == 201


def test_a_pack_that_lacks_one_of_two_needed_capabilities_is_not_enough(world, p):
    define(p, ["booking", "messaging"])
    signed(world, ref="gb-booking-only", manifest=manifest_with("booking"))
    set_country(world, p, "GB")
    assert start(p).status_code == 403
    signed(world, ref="gb-messaging", manifest=manifest_with("messaging"))
    assert start(p).status_code == 201                  # each is switched on by some signed pack


def test_a_suspended_pack_stops_new_workflows_but_running_ones_carry_on(world, p):
    define(p, ["booking"])
    pack = signed(world, ref="gb-care-navigation", manifest=manifest_with("booking"))
    set_country(world, p, "GB")
    running = json.loads(start(p).content)["instance"]
    step(world, pack, "suspend", "iyer", note="Regulator query")
    assert start(p, subject="subject-0002").status_code == 403
    assert fetch(p, running, who="ops")["steps"]                        # still readable


def test_an_expired_pack_does_not_count(world, p):
    define(p, ["booking"])
    pack = signed(world, ref="gb-care-navigation", manifest=manifest_with("booking"))
    set_country(world, p, "GB")
    with tenant_transaction(world.ctx):
        row = CountryPack.objects.get(public_id=pack["id"])
        yesterday = dt.date.today() - dt.timedelta(days=1)
        row.manifest["validity"]["expires_on"] = yesterday.isoformat()
        row.save(update_fields=["manifest"])
    assert start(p).status_code == 403


# ------------------------------------------------------------------ WF-T03: two different packs
def test_the_same_definition_behaves_differently_under_two_different_packs(world, p):
    define(p, ["messaging"])
    signed(world, ref="gb-small", manifest=manifest_with("booking"))
    signed(world, ref="de-wide", country="DE", manifest=manifest_with("booking", "messaging"))
    set_country(world, p, "GB")
    assert start(p).status_code == 403
    set_country(world, p, "DE")
    assert start(p).status_code == 201


def test_the_clinic_cannot_choose_its_country_by_sending_one(world, p):
    define(p, ["booking"])
    signed(world, ref="de-wide", country="DE", manifest=manifest_with("booking"))
    set_country(world, p, "GB")
    r = post(p, "staff/workflows", {
        "definition": "gated-flow", "subject": "subject-0001", "owner": p.desk, "evidence": ORIGIN,
        "purpose": "OPERATIONS", "country": "DE"}, "ops")
    assert r.status_code == 403


# ------------------------------------------------------------------ after it has started (T-62)
def advance_to(p, inst, to, who="ops"):
    from tests.test_orchestration import move
    return move(p, inst, to, who=who)


def test_a_running_workflow_stops_moving_forward_when_its_pack_is_suspended(world, p):
    define(p, ["booking"])
    pack = signed(world, ref="gb-care-navigation", manifest=manifest_with("booking"))
    set_country(world, p, "GB")
    inst = json.loads(start(p).content)["instance"]
    moved = advance_to(p, inst, "validated")
    assert moved.status_code == 200
    inst = json.loads(moved.content)["instance"]
    step(world, pack, "suspend", "iyer", note="Regulator query")
    refused = advance_to(p, inst, "accepted")
    assert refused.status_code == 403
    assert json.loads(refused.content)["code"] == "JURISDICTION_INACTIVE"
    assert fetch(p, inst, who="ops")["steps"][-1]["to_state"] == "validated"      # nothing moved


def test_it_can_still_be_cancelled_when_the_pack_is_suspended(world, p):
    define(p, ["booking"])
    pack = signed(world, ref="gb-care-navigation", manifest=manifest_with("booking"))
    set_country(world, p, "GB")
    inst = json.loads(start(p).content)["instance"]
    step(world, pack, "suspend", "iyer", note="Regulator query")
    assert advance_to(p, inst, "validated").status_code == 403
    cancelled = post(p, f"staff/workflows/{inst['public_id']}/advance", {
        "expected_version": inst["version"], "to_state": "cancelled", "note": "Pack suspended",
        "evidence": ev("cancelled"), "idempotency_key": "cancel-key-0000001"}, "ops")
    assert cancelled.status_code == 200
    assert json.loads(cancelled.content)["instance"]["state"] == "cancelled"


def test_a_workflow_that_needs_no_pack_is_never_stopped_by_one(world, p):
    define(p, None, key="plain-flow")
    inst = json.loads(start(p, key="plain-flow").content)["instance"]
    assert advance_to(p, inst, "validated").status_code == 200
