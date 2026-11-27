"""The referral and waiting-list journeys on the workflow engine (DEV-394, the last part brought
over from the maruthi branch). Their deadlines follow each clinic's OWN settings (response target
in its opening hours, the waiting-list offer window), never a number in the code; a deadline once
set does not move when the setting changes; only an administrator adds one; adding one alone starts
nothing."""
import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.orchestration.models import WorkflowInstance
from modules.trust import api as trust
from tests.test_orchestration import go, post, started, tick
from tests.test_orchestration import p as p  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
ONE_HOUR_A_DAY = {"days": [0, 1, 2, 3, 4, 5, 6], "opens": "09:00", "closes": "10:00"}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.WORKFLOW_CLINICAL_ENABLED = False


def listing(p, who="boss"):
    r = p.get("staff/workflows/definitions", subject=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def add(p, template, who="boss"):
    return post(p, "staff/workflows/definitions", {"template": template}, who)


def in_use(p, template):
    r = add(p, template)
    assert r.status_code == 201, r.content
    made = json.loads(r.content)["definition"]
    r = post(p, f"staff/workflows/definitions/{made['public_id']}/publish", {})
    assert r.status_code == 200, r.content
    return made["key"]


def target(p, hours):
    assert p.post("staff/settings", {"hours": hours}, subject="boss").status_code == 200


def offer_window(p, hours):
    r = p.post("staff/settings", {"waitlist_offer_hours": hours}, subject="boss")
    assert r.status_code == 200, r.content


def hours_from_now(inst):
    due = dt.datetime.fromisoformat(inst["timers"][0]["due_at"])
    return (due - utc_now()).total_seconds() / 3600


# ------------------------------------------------------------------ adding one
def test_both_journeys_are_offered_with_deadlines_from_settings_not_minutes(p):
    data = listing(p, who="ops")
    assert {t["template"] for t in data["templates"]} == {"referral", "waitlist"}
    for t in data["templates"]:
        assert all("after_setting" in tp and "after_minutes" not in tp
                   for tp in t["timer_policies"])
    assert data["can_write"] is False


def test_only_an_administrator_adds_one_once_and_only_a_known_one(p):
    assert add(p, "referral", who="ops").status_code == 403
    r = add(p, "referral")
    assert r.status_code == 201 and json.loads(r.content)["definition"]["status"] == "draft"
    assert add(p, "referral").status_code == 409                      # already added
    for bad in ("triage", "", 7, None):
        assert add(p, bad).status_code == 422


def test_nothing_starts_by_itself(p):
    in_use(p, "referral")
    in_use(p, "waitlist")
    with tenant_transaction(p.clinic.ctx):
        assert WorkflowInstance.objects.count() == 0


# ------------------------------------------------------------------ the clinic's own deadlines
def test_a_referral_must_be_seen_within_the_clinics_target_and_decided_within_three_times(p):
    target(p, 4)
    key = in_use(p, "referral")
    inst = started(p, key=key)
    assert inst["timers"][0]["kind"] == "clinic_response_overdue"
    assert 3.9 < hours_from_now(inst) <= 4.0
    seen = go(p, inst, "validated")
    assert 11.9 < hours_from_now(seen) <= 12.0


def test_a_changed_setting_does_not_move_a_deadline_already_set(p):
    target(p, 4)
    inst = started(p, key=in_use(p, "referral"))
    target(p, 48)
    assert tick(p, 5 * 60)["fired"] == 1                             # still the 4-hour deadline
    assert started(p, key="referral-journey", subject="subject-0002")["timers"][0]["due_at"] \
        > inst["timers"][0]["due_at"]                                 # new ones use 48 hours


def test_the_deadline_counts_only_the_clinics_opening_hours(p):
    target(p, 1)
    with tenant_transaction(p.clinic.ctx):
        trust.set_business_hours(p.clinic.org, ONE_HOUR_A_DAY, by="system")
    inst = started(p, key=in_use(p, "referral"))
    assert hours_from_now(inst) > 1.0                                 # not one clock hour later


def test_an_offered_waiting_list_time_must_be_used_within_the_clinics_window(p):
    target(p, 4)
    offer_window(p, 6)
    inst = started(p, key=in_use(p, "waitlist"))
    assert 3.9 < hours_from_now(inst) <= 4.0
    inst = go(p, go(p, inst, "validated"), "accepted")
    assert inst["timers"][0]["kind"] == "offer_overdue"
    assert 5.9 < hours_from_now(inst) <= 6.0


def test_a_missed_deadline_opens_an_escalation_for_a_person_as_usual(p):
    target(p, 4)
    started(p, key=in_use(p, "referral"))
    assert tick(p, 3 * 60)["fired"] == 0
    assert tick(p, 5 * 60)["fired"] == 1


# ------------------------------------------------------------------ writing a setting-based timer
@pytest.mark.parametrize("timer", [
    {"state": "requested", "kind": "clinic_response_overdue", "after_setting": "made_up"},
    {"state": "requested", "kind": "clinic_response_overdue", "after_setting": "offer_window",
     "times": 0},
    {"state": "requested", "kind": "clinic_response_overdue", "after_setting": "offer_window",
     "times": 11},
    {"state": "requested", "kind": "clinic_response_overdue", "after_setting": "offer_window",
     "times": True},
    {"state": "requested", "kind": "clinic_response_overdue", "after_setting": "offer_window",
     "after_minutes": 30},
    {"state": "closed", "kind": "clinic_response_overdue", "after_setting": "response_target"},
    {"state": "requested", "kind": "made_up", "after_setting": "response_target"},
])
def test_a_setting_based_timer_must_name_a_real_setting_and_a_sane_multiple(p, timer):
    r = post(p, "staff/workflows/definitions", {
        "key": "own-flow", "journey": "visit",
        "path": ["requested", "validated", "accepted", "in_progress", "output_available",
                 "closed"], "timer_policies": [timer]})
    assert r.status_code == 422
