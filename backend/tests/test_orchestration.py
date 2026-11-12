"""The workflow engine kernel (GHE-12; DEV-132): frozen versioned definitions, evidence-gated
moves with compare-and-swap, an independent health overlay, timers that open escalations for a named
person, and one tamper-evident history."""
import datetime as dt
import itertools
import json

import pytest
from django.db import DatabaseError, transaction

from foundation.cell_context import tenant_transaction
from foundation.timeutil import FixedClock
from modules.evidence.models import AuditEvent
from modules.orchestration import api as orchestration
from modules.orchestration.engine import EDGE_EVIDENCE, REFUSED_KINDS
from modules.orchestration.models import Transition
from modules.trust import api as trust
from tests.test_api import Web
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
PATH = ["requested", "validated", "accepted", "in_progress", "output_available", "closed"]
CLINICAL_PATH = ["requested", "validated", "accepted", "in_progress", "output_available",
                 "clinical_reviewed", "handoff_acknowledged", "closed"]
ORIGIN = [{"kind": "request_origin", "ref": "origin-0001"}]
_counter = itertools.count(1)


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.WORKFLOW_CLINICAL_ENABLED = False


@pytest.fixture
def p():
    practice = Practice()
    practice.boss_id = practice.login("boss", trust.ActorType.FACILITY_USER, "R13")
    practice.ops2_id = practice.login("ops2", trust.ActorType.FACILITY_USER, "R04")
    return practice


def ev(state):
    return [{"kind": EDGE_EVIDENCE.get(state, "validation"), "ref": f"ref-{state}-0001"}]


def post(p, path, body, who="boss"):
    return p.post(path, body, subject=who)


def define(p, key="visit-flow", path=None, timers=None, publish=True, journey="visit"):
    r = post(p, "staff/workflows/definitions", {
        "key": key, "journey": journey, "path": path or PATH, "timer_policies": timers or []})
    assert r.status_code == 201, r.content
    made = json.loads(r.content)["definition"]
    if publish:
        r = post(p, f"staff/workflows/definitions/{made['public_id']}/publish", {})
        assert r.status_code == 200, r.content
        made = json.loads(r.content)["definition"]
    return made


def begin(p, key="visit-flow", subject="subject-0001", owner=None, who="ops", evidence=ORIGIN):
    return post(p, "staff/workflows", {
        "definition": key, "subject": subject, "owner": owner or p.desk, "evidence": evidence,
        "purpose": "OPERATIONS"}, who)


def started(p, **kw):
    r = begin(p, **kw)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["instance"]


def move(p, inst, to, who="ops", version=None, evidence=None, key=None, **extra):
    return post(p, f"staff/workflows/{inst['public_id']}/advance", {
        "expected_version": inst["version"] if version is None else version, "to_state": to,
        "evidence": ev(to) if evidence is None else evidence,
        "idempotency_key": key or f"idem-{next(_counter):08d}", **extra}, who)


def go(p, inst, to, **kw):
    r = move(p, inst, to, **kw)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["instance"]


def walk(p, inst, path=PATH, upto=None, who="ops"):
    for state in path[1:]:
        inst = go(p, inst, state, who=who)
        if state == upto:
            break
    return inst


def fetch(p, inst, who="boss"):
    r = p.get(f"staff/workflows/{inst['public_id']}", subject=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def current(p, inst):
    return fetch(p, inst)["instance"]


def overlay(p, inst, value, note="Partner has not answered", who="ops", evidence=None):
    body = {"expected_version": inst["version"], "overlay": value, "note": note}
    if evidence is not None:
        body["evidence"] = evidence
    return post(p, f"staff/workflows/{inst['public_id']}/overlay", body, who)


RESOLVED = [{"kind": "exception_resolution", "ref": "resolution-0001"}]


def tick(p, minutes):
    with tenant_transaction(p.clinic.ctx):
        return orchestration.fire_due_timers(
            clock=FixedClock(dt.datetime.now(dt.UTC) + dt.timedelta(minutes=minutes)))


def escalations(p, who="boss"):
    return json.loads(p.get("staff/workflows/escalations", subject=who).content)["escalations"]


# ------------------------------------------------------------------ definitions
def test_only_a_clinic_administrator_writes_definitions(p):
    body = {"key": "visit-flow", "journey": "visit", "path": PATH, "timer_policies": []}
    for who in ("ops", "doc", "pat"):
        assert post(p, "staff/workflows/definitions", body, who).status_code == 403
    assert post(p, "staff/workflows/definitions", body).status_code == 201
    assert post(p, "staff/workflows/definitions", body).status_code == 409        # key taken


@pytest.mark.parametrize("path", [
    [], "requested", PATH[:-1], PATH[1:], [PATH[0], PATH[2], PATH[1], *PATH[3:]],
    [*PATH[:2], *PATH[3:]],                                  # accepted is not optional
    [*PATH[:3], *PATH[4:]],                                  # in progress is not optional
    [*PATH, "closed"], [*PATH[:-1], "teleported", "closed"], [*PATH[:-1], 7, "closed"],
])
def test_a_path_must_be_the_universal_states_in_order_with_only_review_and_handoff_optional(
        p, path):
    r = post(p, "staff/workflows/definitions", {
        "key": "bad-path", "journey": "visit", "path": path, "timer_policies": []})
    assert r.status_code == 422


@pytest.mark.parametrize("timers", [
    [{"state": "accepted", "kind": "partner_response_overdue"}],
    [{"state": "accepted", "kind": "partner_response_overdue", "after_minutes": 0}],
    [{"state": "accepted", "kind": "partner_response_overdue", "after_minutes": 600000}],
    [{"state": "accepted", "kind": "partner_response_overdue", "after_minutes": True}],
    [{"state": "accepted", "kind": "partner_response_overdue", "after_minutes": "30"}],
    [{"state": "accepted", "kind": "made_up", "after_minutes": 30}],
    [{"state": "clinical_reviewed", "kind": "clinical_review_overdue", "after_minutes": 30}],
    [{"state": "closed", "kind": "handoff_overdue", "after_minutes": 30}],
    [{"state": "accepted", "kind": "handoff_overdue", "after_minutes": 30, "extra": 1}],
    [{"state": "accepted", "kind": "handoff_overdue", "after_minutes": 1}] * 7,
    "soon",
])
def test_a_timer_policy_must_state_its_own_minutes_there_is_no_default(p, timers):
    r = post(p, "staff/workflows/definitions", {
        "key": "bad-timer", "journey": "visit", "path": PATH, "timer_policies": timers})
    assert r.status_code == 422


@pytest.mark.parametrize("key,journey", [("A", "visit"), ("ab", "visit"), ("Visit Flow", "visit"),
                                         ("visit-flow", "Visit"), ("visit-flow", "")])
def test_the_name_and_journey_must_be_well_formed(p, key, journey):
    r = post(p, "staff/workflows/definitions", {
        "key": key, "journey": journey, "path": PATH, "timer_policies": []})
    assert r.status_code == 422


def test_a_published_definition_is_frozen(p):
    d = define(p)
    assert d["status"] == "published" and len(d["content_hash"]) == 64
    r = post(p, f"staff/workflows/definitions/{d['public_id']}/edit",
             {"path": PATH, "timer_policies": []})
    assert r.status_code == 409
    again = post(p, f"staff/workflows/definitions/{d['public_id']}/publish", {})
    assert again.status_code == 200                                         # harmless


def test_a_draft_can_be_edited_and_its_fingerprint_follows(p):
    d = define(p, publish=False)
    r = post(p, f"staff/workflows/definitions/{d['public_id']}/edit", {
        "path": CLINICAL_PATH, "timer_policies": []})
    assert r.status_code == 200
    assert json.loads(r.content)["definition"]["content_hash"] != d["content_hash"]


def test_a_clinical_path_cannot_be_published_while_clinical_workflows_are_off(p, settings):
    d = define(p, key="lab-flow", path=CLINICAL_PATH, publish=False)
    assert d["clinical"] is True
    assert post(p, f"staff/workflows/definitions/{d['public_id']}/publish", {}
                ).status_code == 403
    settings.WORKFLOW_CLINICAL_ENABLED = True
    assert post(p, f"staff/workflows/definitions/{d['public_id']}/publish", {}
                ).status_code == 200


def test_a_change_is_a_new_version_and_running_instances_stay_on_theirs(p):
    v1 = define(p)
    old = started(p, subject="subject-0001")
    v2 = json.loads(post(p, f"staff/workflows/definitions/{v1['public_id']}/version", {}
                         ).content)["definition"]
    assert v2["version"] == 2 and v2["status"] == "draft"
    assert post(p, f"staff/workflows/definitions/{v1['public_id']}/version", {}
                ).status_code == 409                                         # one draft at a time
    post(p, f"staff/workflows/definitions/{v2['public_id']}/edit", {
        "path": PATH, "timer_policies": [
            {"state": "accepted", "kind": "partner_response_overdue", "after_minutes": 60}]})
    post(p, f"staff/workflows/definitions/{v2['public_id']}/publish", {})
    fresh = started(p, subject="subject-0002")
    assert current(p, old)["definition_version"] == 1 and fresh["definition_version"] == 2


def test_a_retired_definition_starts_nothing_new_but_running_work_continues(p):
    d = define(p)
    running = started(p)
    assert post(p, f"staff/workflows/definitions/{d['public_id']}/retire", {}).status_code == 200
    assert begin(p, subject="subject-0002").status_code == 404
    assert go(p, running, "validated")["state"] == "validated"


# ------------------------------------------------------------------ starting
def test_a_workflow_starts_only_from_a_published_definition(p):
    define(p, key="draft-only", publish=False)
    assert begin(p, key="draft-only").status_code == 404
    assert begin(p, key="never-made").status_code == 404


def test_starting_needs_the_origin_a_real_owner_and_a_proper_subject(p):
    define(p)
    assert begin(p, evidence=[]).status_code == 422
    assert begin(p, evidence=[{"kind": "validation", "ref": "ref-0001"}]).status_code == 422
    assert begin(p, owner="nobody-real-0000000000").status_code == 422
    assert begin(p, subject="x").status_code == 422
    assert begin(p, subject="has spaces here").status_code == 422
    assert begin(p, who="doc").status_code == 403
    assert begin(p, who="pat").status_code == 403


def test_one_subject_has_one_live_workflow_but_may_start_again_after_it_ends(p):
    define(p)
    first = started(p)
    assert begin(p).status_code == 409
    post(p, f"staff/workflows/{first['public_id']}/advance", {
        "expected_version": first["version"], "to_state": "cancelled", "evidence": ev("cancelled"),
        "idempotency_key": "idem-cancel-0001", "note": "Request withdrawn"}, "ops")
    assert begin(p).status_code == 201


def test_a_new_workflow_begins_requested_clear_with_its_owner_and_one_step(p):
    define(p)
    inst = started(p)
    assert inst["state"] == "requested" and inst["overlay"] == "clear" and inst["owner"] == p.desk
    assert inst["version"] == 1 and inst["path"] == PATH
    steps = fetch(p, inst)["steps"]
    assert len(steps) == 1 and steps[0]["to_state"] == "requested"
    assert steps[0]["definition_version"] == 1 and steps[0]["decision"]


# ------------------------------------------------------------------ moving on evidence
def test_the_whole_path_closes_with_a_digest_and_an_intact_history(p):
    define(p)
    inst = walk(p, started(p))
    assert inst["state"] == "closed" and inst["closed_at"] and len(inst["closure_digest"]) == 64
    full = fetch(p, inst)
    assert full["chain_ok"] is True and len(full["steps"]) == len(PATH)
    assert [s["to_state"] for s in full["steps"]] == PATH


@pytest.mark.parametrize("kind", sorted(REFUSED_KINDS))
def test_a_receipt_or_a_silence_is_never_evidence(p, kind):
    define(p)
    inst = started(p)
    r = move(p, inst, "validated", evidence=[{"kind": kind, "ref": "ref-0001"}])
    assert r.status_code == 422 and current(p, inst)["state"] == "requested"


@pytest.mark.parametrize("evidence", [
    [], None, "validation", [{"kind": "acceptance_attestation", "ref": "ref-0001"}],
    [{"kind": "validation"}], [{"kind": "validation", "ref": "x"}],
    [{"kind": "validation", "ref": "has spaces in it"}],
    [{"kind": "validation", "ref": "ref-0001", "extra": 1}],
    [{"kind": "made_up", "ref": "ref-0001"}], [{"kind": "validation", "ref": 12345}],
    [{"kind": "validation", "ref": "ref-0001"}] * 9,
])
def test_evidence_must_be_typed_small_and_the_kind_the_step_needs(p, evidence):
    define(p)
    inst = started(p)
    r = post(p, f"staff/workflows/{inst['public_id']}/advance", {
        "expected_version": inst["version"], "to_state": "validated", "evidence": evidence,
        "idempotency_key": "idem-evidence-0001"}, "ops")
    assert r.status_code == 422 and current(p, inst)["state"] == "requested"


def test_extra_evidence_of_a_known_kind_may_come_along(p):
    define(p)
    inst = started(p)
    more = ev("validated") + [{"kind": "request_origin", "ref": "origin-0002"}]
    assert go(p, inst, "validated", evidence=more)["state"] == "validated"


@pytest.mark.parametrize("to", ["accepted", "closed", "requested", "output_available", "nonsense"])
def test_no_shortcut_and_no_going_back(p, to):
    define(p)
    inst = started(p)
    assert move(p, inst, to).status_code == 422
    assert current(p, inst)["state"] == "requested"


def test_a_stale_version_is_a_conflict_not_a_retry(p):
    define(p)
    inst = started(p)
    go(p, inst, "validated")
    assert move(p, inst, "validated", version=inst["version"]).status_code == 409   # old copy
    assert move(p, inst, "accepted", version=99).status_code == 409
    assert current(p, inst)["state"] == "validated"


def test_the_same_request_again_does_nothing_twice(p):
    define(p)
    inst = started(p)
    first = move(p, inst, "validated", key="idem-same-key-01")
    again = move(p, inst, "validated", key="idem-same-key-01")
    assert first.status_code == again.status_code == 200
    assert len(fetch(p, inst)["steps"]) == 2
    assert move(p, inst, "accepted", key="idem-same-key-01", version=2).status_code == 409


@pytest.mark.parametrize("key", ["", "short", "has spaces here", "x" * 70])
def test_an_idempotency_key_is_required_and_well_formed(p, key):
    define(p)
    inst = started(p)
    r = post(p, f"staff/workflows/{inst['public_id']}/advance", {
        "expected_version": 1, "to_state": "validated", "evidence": ev("validated"),
        "idempotency_key": key}, "ops")
    assert r.status_code == 422


def test_only_the_accountable_owner_or_an_administrator_may_move_it(p):
    define(p)
    inst = started(p)                                         # owned by "ops"
    assert move(p, inst, "validated", who="ops2").status_code == 403      # another staff member
    assert move(p, inst, "validated", who="doc").status_code == 403
    assert current(p, inst)["state"] == "requested"
    assert go(p, inst, "validated", who="boss")["state"] == "validated"   # an administrator


def test_a_clinical_path_needs_review_and_handoff_evidence_when_it_has_them(p, settings):
    settings.WORKFLOW_CLINICAL_ENABLED = True
    define(p, key="lab-flow", path=CLINICAL_PATH)
    inst = started(p, key="lab-flow")
    inst = walk(p, inst, CLINICAL_PATH, upto="output_available")
    assert move(p, inst, "handoff_acknowledged").status_code == 422        # not before review
    reviewed = go(p, inst, "clinical_reviewed")
    assert move(p, reviewed, "closed").status_code == 422                  # handoff is required
    handed = go(p, reviewed, "handoff_acknowledged")
    assert go(p, handed, "closed")["state"] == "closed"


def test_a_non_clinical_path_never_invents_a_review_step(p):
    define(p)
    inst = walk(p, started(p), PATH, upto="output_available")
    assert move(p, inst, "clinical_reviewed").status_code == 422
    assert go(p, inst, "closed")["state"] == "closed"


# ------------------------------------------------------------------ health overlay and closure
def test_health_is_a_separate_dimension_that_never_moves_the_state(p):
    define(p)
    inst = started(p)
    r = overlay(p, inst, "at_risk")
    marked = json.loads(r.content)["instance"]
    assert r.status_code == 200 and marked["overlay"] == "at_risk"
    assert marked["state"] == "requested"
    assert marked["version"] == inst["version"] + 1
    steps = fetch(p, marked)["steps"]
    assert steps[-1]["kind"] == "overlay" and steps[-1]["to_state"] == "requested"


def test_changing_health_needs_a_reason_and_a_valid_value(p):
    define(p)
    inst = started(p)
    assert overlay(p, inst, "at_risk", note="").status_code == 422
    assert overlay(p, inst, "fine").status_code == 422
    assert overlay(p, inst, "clear").status_code == 200                    # same value: nothing


def test_it_cannot_close_with_an_open_exception_or_while_a_person_is_still_owed_an_answer(p):
    define(p)
    inst = walk(p, started(p), PATH, upto="output_available")
    flagged = json.loads(
        overlay(p, inst, "exception", "Patient match in doubt").content)["instance"]
    r = move(p, flagged, "closed")
    assert r.status_code == 422 and "open_exception" in r.json().get("fields", [])
    cleared = json.loads(overlay(p, flagged, "clear", "Match confirmed by the clinician",
                                 evidence=RESOLVED).content)["instance"]
    assert go(p, cleared, "closed")["state"] == "closed"


def test_a_closed_workflow_cannot_change_again(p):
    define(p)
    inst = walk(p, started(p))
    assert move(p, inst, "validated").status_code == 409
    assert overlay(p, inst, "at_risk").status_code == 409
    assert post(p, f"staff/workflows/{inst['public_id']}/reassign", {
        "expected_version": inst["version"], "owner": p.ops2_id}).status_code == 409


# ------------------------------------------------------------------ blocking, cancelling
def test_blocking_needs_a_reason_evidence_and_a_named_incident_owner(p):
    define(p)
    inst = started(p)
    base = {"expected_version": 1, "to_state": "blocked", "evidence": ev("blocked"),
            "idempotency_key": "idem-block-0001", "note": "Identity doubt",
            "incident_owner": p.ops2_id}
    for bad in ({"note": ""}, {"evidence": []}, {"incident_owner": ""},
                {"incident_owner": "nobody-real-0000000000"}):
        r = post(p, f"staff/workflows/{inst['public_id']}/advance",
                 {**base, **bad, "idempotency_key": f"idem-b-{next(_counter):08d}"}, "ops")
        assert r.status_code == 422
    r = post(p, f"staff/workflows/{inst['public_id']}/advance", base, "ops")
    blocked = json.loads(r.content)["instance"]
    assert blocked["state"] == "blocked" and blocked["blocked_owner"] == p.ops2_id


def test_nothing_moves_while_blocked_and_only_the_incident_owner_or_admin_lifts_it(p):
    define(p)
    inst = walk(p, started(p), PATH, upto="accepted")
    r = post(p, f"staff/workflows/{inst['public_id']}/advance", {
        "expected_version": inst["version"], "to_state": "blocked", "evidence": ev("blocked"),
        "idempotency_key": "idem-block-0002", "note": "Safety query",
        "incident_owner": p.ops2_id}, "ops")
    blocked = json.loads(r.content)["instance"]
    assert move(p, blocked, "in_progress").status_code == 409
    lift = {"expected_version": blocked["version"], "note": "Query answered",
            "evidence": [{"kind": "block_resolution", "ref": "ref-0001"}]}
    assert post(p, f"staff/workflows/{inst['public_id']}/resume", lift, "ops").status_code == 403
    assert post(p, f"staff/workflows/{inst['public_id']}/resume",
                {**lift, "evidence": ev("blocked")}, "ops2").status_code == 422
    r = post(p, f"staff/workflows/{inst['public_id']}/resume", lift, "ops2")
    back = json.loads(r.content)["instance"]
    assert r.status_code == 200 and back["state"] == "accepted"
    assert go(p, back, "in_progress")["state"] == "in_progress"


def test_cancelling_needs_a_reason_and_authorisation_evidence_and_is_final(p):
    define(p)
    inst = started(p)
    base = {"expected_version": 1, "to_state": "cancelled", "evidence": ev("cancelled"),
            "idempotency_key": "idem-cancel-0002", "note": "Request withdrawn by the clinic"}
    assert post(p, f"staff/workflows/{inst['public_id']}/advance",
                {**base, "note": ""}, "ops").status_code == 422
    assert post(p, f"staff/workflows/{inst['public_id']}/advance",
                {**base, "evidence": [], "idempotency_key": "idem-cancel-0003"},
                "ops").status_code == 422
    r = post(p, f"staff/workflows/{inst['public_id']}/advance", base, "ops")
    assert r.status_code == 200 and json.loads(r.content)["instance"]["state"] == "cancelled"
    assert move(p, json.loads(r.content)["instance"], "validated").status_code == 409


# ------------------------------------------------------------------ timers and escalation
TIMERS = [{"state": "accepted", "kind": "partner_response_overdue", "after_minutes": 60}]


def accepted(p, subject="subject-0001"):
    define(p, timers=TIMERS)
    return walk(p, started(p, subject=subject), PATH, upto="accepted")


def test_entering_a_state_starts_its_timers_and_leaving_it_stops_them(p):
    inst = accepted(p)
    assert [t["kind"] for t in inst["timers"]] == ["partner_response_overdue"]
    assert inst["timers"][0]["watches"] == "accepted"
    moved = go(p, inst, "in_progress")
    assert moved["timers"] == []


def test_an_expired_timer_marks_it_at_risk_and_opens_an_escalation_for_the_named_owner(p):
    inst = accepted(p)
    assert tick(p, 30)["fired"] == 0                                      # not due yet
    assert tick(p, 120) == {"fired": 1, "stale": 0}
    now = current(p, inst)
    assert now["state"] == "accepted" and now["overlay"] == "at_risk"
    assert now["open_escalations"] == 1
    [esc] = escalations(p)
    assert esc["owner"] == p.desk and esc["status"] == "open" and esc["attempts"] == 0
    last = fetch(p, inst)["steps"][-1]
    assert last["kind"] == "timer" and last["actor"] == "system"


def test_a_timer_for_a_step_that_is_over_does_nothing(p):
    inst = accepted(p)
    with tenant_transaction(p.clinic.ctx):
        from modules.orchestration.models import WorkflowTimer
        WorkflowTimer.objects.filter(status="pending").update(
            due_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1))
    go(p, inst, "in_progress")                                            # the step is left
    assert tick(p, 0)["fired"] == 0 and escalations(p) == []
    assert current(p, inst)["overlay"] == "clear"


def test_only_a_person_closes_an_escalation_and_a_failed_try_is_counted(p):
    inst = accepted(p)
    tick(p, 120)
    [esc] = escalations(p)
    url = f"staff/workflows/escalations/{esc['public_id']}/record"
    assert post(p, url, {"reached": True, "note": ""}, "ops").status_code == 422
    assert post(p, url, {"reached": "yes", "note": "Spoke to the lab"}, "ops").status_code == 422
    assert post(p, url, {"reached": True, "note": "Spoke to the lab"}, "ops2").status_code == 403
    missed = json.loads(post(p, url, {"reached": False, "note": "No answer from the lab",
                                      "fallback_owner": p.ops2_id}, "ops").content)["escalation"]
    assert missed["status"] == "open" and missed["attempts"] == 1 and missed["owner"] == p.ops2_id
    done = json.loads(post(p, url, {"reached": True, "note": "Spoke to the on-call lead"},
                           "ops2").content)["escalation"]
    assert done["status"] == "reached" and escalations(p) == []
    assert post(p, url, {"reached": False, "note": "Late attempt"}, "ops2").status_code == 200
    assert current(p, inst)["overlay"] == "at_risk"                       # a person must clear it


def test_health_cannot_be_cleared_while_an_escalation_still_waits_for_a_person(p):
    inst = accepted(p)
    tick(p, 120)
    now = current(p, inst)
    assert overlay(p, now, "clear", "Looks fine to me").status_code == 409
    [esc] = escalations(p)
    post(p, f"staff/workflows/escalations/{esc['public_id']}/record",
         {"reached": True, "note": "Lead took ownership"}, "ops")
    assert overlay(p, current(p, inst), "clear", "Lead has it in hand").status_code == 200


def test_it_cannot_close_while_an_escalation_waits(p):
    define(p, timers=[{"state": "output_available", "kind": "partner_response_overdue",
                       "after_minutes": 5}])
    inst = walk(p, started(p), PATH, upto="output_available")
    tick(p, 60)
    cleared = current(p, inst)
    r = move(p, cleared, "closed")
    assert r.status_code == 422 and "open_escalation" in r.json().get("fields", [])


def test_reassigning_moves_the_timers_and_escalations_to_the_new_person(p):
    inst = accepted(p)
    tick(p, 120)
    now = current(p, inst)
    url = f"staff/workflows/{inst['public_id']}/reassign"
    assert post(p, url, {"expected_version": now["version"], "owner": p.ops2_id},
                "ops").status_code == 403                                  # not the owner's call
    assert post(p, url, {"expected_version": 99, "owner": p.ops2_id}).status_code == 409
    assert post(p, url, {"expected_version": now["version"], "owner": "nobody-real-0000000000"}
                ).status_code == 422
    r = post(p, url, {"expected_version": now["version"], "owner": p.ops2_id})
    assert r.status_code == 200 and json.loads(r.content)["instance"]["owner"] == p.ops2_id
    assert escalations(p)[0]["owner"] == p.ops2_id
    assert fetch(p, inst)["steps"][-1]["kind"] == "owner"


# ------------------------------------------------------------------ the workbench and the record
def test_the_workbench_lists_only_what_needs_a_person(p):
    define(p, key="quiet-flow", timers=[])
    define(p, key="timed-flow", timers=TIMERS)
    calm = started(p, key="quiet-flow", subject="subject-0001")
    risky = started(p, key="quiet-flow", subject="subject-0002")
    overlay(p, risky, "exception", "Source and booking disagree")
    timed = walk(p, started(p, key="timed-flow", subject="subject-0003"), PATH, upto="accepted")
    tick(p, 120)
    closed = walk(p, started(p, key="quiet-flow", subject="subject-0004"))
    r = p.get("staff/workflows?view=workbench", subject="boss")
    ids = {i["public_id"] for i in json.loads(r.content)["instances"]}
    assert ids == {risky["public_id"], timed["public_id"]}
    assert calm["public_id"] not in ids and closed["public_id"] not in ids


def test_a_listing_can_be_filtered_by_state(p):
    define(p)
    one = started(p, subject="subject-0001")
    go(p, started(p, subject="subject-0002"), "validated")
    r = p.get("staff/workflows?state=requested", subject="boss")
    assert [i["public_id"] for i in json.loads(r.content)["instances"]] == [one["public_id"]]
    assert p.get("staff/workflows?state=nonsense", subject="boss").status_code == 422


def test_the_history_cannot_be_changed_or_removed_even_with_direct_access(p):
    define(p)
    inst = walk(p, started(p), PATH, upto="validated")
    with tenant_transaction(p.clinic.ctx):
        mine = Transition.objects.filter(instance__public_id=inst["public_id"])
        with pytest.raises(DatabaseError), transaction.atomic():
            mine.update(note="edited")
        with pytest.raises(DatabaseError), transaction.atomic():
            mine.delete()
    assert fetch(p, inst)["chain_ok"] is True


def test_every_step_records_who_what_version_and_the_rules_in_force(p):
    define(p)
    inst = walk(p, started(p), PATH, upto="validated")
    with tenant_transaction(p.clinic.ctx):
        rows = list(Transition.objects.filter(instance__public_id=inst["public_id"])
                    .order_by("seq"))
    assert [r.seq for r in rows] == [1, 2]
    for r in rows:
        assert r.actor_ref == p.desk and r.role_code == "R04" and r.definition_version == 1
        assert len(r.policy_digest) == 64 and r.decision_ref and r.evidence


def test_changes_are_audited(p):
    d = define(p)
    inst = walk(p, started(p), PATH, upto="validated")
    with tenant_transaction(p.clinic.ctx):
        actions = [e.action for e in AuditEvent.objects.filter(
            action__startswith="orchestration.").order_by("chain_seq")]
    assert actions == [
        "orchestration.definition_created", "orchestration.definition_published",
        "orchestration.requested", "orchestration.validated"]
    assert d["public_id"] and inst["public_id"]


def test_another_customer_sees_and_changes_nothing(p):
    define(p)
    inst = started(p)
    outsider = Web()
    outsider.login("admin2", trust.ActorType.FACILITY_USER, "R13")
    assert json.loads(outsider.get("staff/workflows", subject="admin2").content
                      )["instances"] == []
    assert json.loads(outsider.get("staff/workflows/definitions", subject="admin2").content
                      )["definitions"] == []
    assert outsider.get(f"staff/workflows/{inst['public_id']}", subject="admin2"
                        ).status_code == 404
    assert outsider.post(f"staff/workflows/{inst['public_id']}/overlay", {
        "expected_version": 1, "overlay": "at_risk", "note": "Not yours"},
        subject="admin2").status_code == 404


def test_patients_and_doctors_cannot_use_the_engine(p):
    define(p)
    inst = started(p)
    for who in ("pat", "doc"):
        assert p.get("staff/workflows", subject=who).status_code == 403
        assert p.get("staff/workflows/definitions", subject=who).status_code == 403
        assert p.get(f"staff/workflows/{inst['public_id']}", subject=who).status_code == 403
        assert p.get("staff/workflows/escalations", subject=who).status_code == 403


# ---------------------------------------------------- replay and holding (T32, T33, T36, T53)
def test_the_state_can_be_rebuilt_from_the_history_alone_and_agrees(p):
    define(p)
    inst = walk(p, started(p), PATH, upto="accepted")
    inst = json.loads(overlay(p, inst, "at_risk", "Partner slow to answer").content)["instance"]
    check = fetch(p, inst)["replay"]
    assert check == {"ok": True, "problems": [], "first_bad_step": None, "steps": 4}
    assert check["steps"] == inst["version"]


@pytest.mark.parametrize("field,value,problem", [
    ("state", "closed", "state"), ("overlay", "exception", "overlay"),
    ("entity_version", 40, "version")])
def test_a_workflow_that_disagrees_with_its_history_is_found_and_frozen(p, field, value, problem):
    from modules.orchestration.models import WorkflowInstance
    define(p)
    inst = walk(p, started(p), PATH, upto="validated")
    with tenant_transaction(p.clinic.ctx):
        WorkflowInstance.objects.filter(public_id=inst["public_id"]).update(**{field: value})
    seen = fetch(p, inst)["replay"]
    assert seen["ok"] is False and problem in seen["problems"]
    frozen = current(p, inst)
    for r in (move(p, frozen, "accepted", version=frozen["version"]),
              overlay(p, frozen, "at_risk")):
        assert r.status_code >= 400 and r.json()["code"] == "HOLD_ACTIVE"
    assert post(p, f"staff/workflows/{inst['public_id']}/reassign", {
        "expected_version": frozen["version"], "owner": p.ops2_id}).json()["code"] == "HOLD_ACTIVE"


def test_an_administrator_cannot_enter_a_review_or_handoff_for_the_owner(p, settings):
    settings.WORKFLOW_CLINICAL_ENABLED = True
    define(p, key="lab-flow", path=CLINICAL_PATH)
    inst = walk(p, started(p, key="lab-flow"), CLINICAL_PATH, upto="output_available")
    assert move(p, inst, "clinical_reviewed", who="boss").status_code == 403
    assert current(p, inst)["state"] == "output_available"
    reviewed = go(p, inst, "clinical_reviewed", who="ops")                 # the accountable owner
    assert move(p, reviewed, "handoff_acknowledged", who="boss").status_code == 403
    # An administrator who wants to act must first take responsibility, which is on the record.
    r = post(p, f"staff/workflows/{inst['public_id']}/reassign", {
        "expected_version": reviewed["version"], "owner": p.boss_id})
    mine_now = json.loads(r.content)["instance"]
    assert go(p, mine_now, "handoff_acknowledged", who="boss")["state"] == "handoff_acknowledged"
    assert [s["kind"] for s in fetch(p, inst)["steps"]].count("owner") == 1


# ---------------------------------------------------- starting is idempotent (WF-T13, T14)
def begin_with(p, key, **over):
    body = {"definition": "visit-flow", "subject": "subject-0001", "owner": p.desk,
            "evidence": ORIGIN, "purpose": "OPERATIONS", "idempotency_key": key, **over}
    return post(p, "staff/workflows", body, "ops")


def test_starting_twice_with_the_same_key_gives_one_workflow_and_the_same_answer(p):
    define(p)
    first = begin_with(p, "start-key-0001")
    again = begin_with(p, "start-key-0001")
    assert first.status_code == 201 and again.status_code == 200
    a, b = json.loads(first.content)["instance"], json.loads(again.content)["instance"]
    assert a["public_id"] == b["public_id"] and a["replayed"] is False and b["replayed"] is True
    assert len(json.loads(p.get("staff/workflows", subject="boss").content)["instances"]) == 1
    assert len(fetch(p, a)["steps"]) == 1


def test_the_same_key_with_a_different_request_is_a_conflict_and_changes_nothing(p):
    define(p)
    begin_with(p, "start-key-0002")
    for change in ({"subject": "subject-0099"}, {"owner": p.ops2_id}):
        assert begin_with(p, "start-key-0002", **change).status_code == 409
    assert len(json.loads(p.get("staff/workflows", subject="boss").content)["instances"]) == 1


def test_a_key_must_be_well_formed_and_without_one_the_old_rule_still_applies(p):
    define(p)
    for bad in ("short", "has spaces here", "x" * 70):
        assert begin_with(p, bad).status_code == 422
    assert begin_with(p, "").status_code == 201
    assert begin_with(p, "").status_code == 409                # one live workflow per subject


def test_two_different_keys_for_the_same_subject_still_cannot_make_two_workflows(p):
    define(p)
    assert begin_with(p, "start-key-0003").status_code == 201
    assert begin_with(p, "start-key-0004").status_code == 409


def test_one_customers_key_does_not_collide_with_anothers(p):
    define(p)
    begin_with(p, "start-key-0005")
    outsider = Web()
    outsider.login("admin2", trust.ActorType.FACILITY_USER, "R13")
    outsider.login("desk2", trust.ActorType.FACILITY_USER, "R04")
    mine = json.loads(outsider.post("staff/workflows/definitions", {
        "key": "visit-flow", "journey": "visit", "path": PATH, "timer_policies": []},
        subject="admin2").content)["definition"]
    outsider.post(f"staff/workflows/definitions/{mine['public_id']}/publish", {}, subject="admin2")
    r = outsider.post("staff/workflows", {
        "definition": "visit-flow", "subject": "subject-0001", "owner": "x" * 22,
        "evidence": ORIGIN, "idempotency_key": "start-key-0005"}, subject="desk2")
    assert r.status_code == 422                                # (no such owner), not a conflict


# ----------------------------------------------- clearing an exception needs evidence (T54)
@pytest.mark.parametrize("value", ["exception", "reconciling"])
def test_an_exception_or_a_reconciliation_cannot_be_cleared_on_someones_word_alone(p, value):
    define(p)
    inst = started(p)
    flagged = json.loads(overlay(p, inst, value, "Source and booking disagree").content)["instance"]
    assert overlay(p, flagged, "clear", "It is fine now").status_code == 422
    wrong = [{"kind": "validation", "ref": "ref-0001"}]
    assert overlay(p, flagged, "clear", "It is fine now", evidence=wrong).status_code == 422
    assert overlay(p, flagged, "clear", "It is fine now", evidence=[]).status_code == 422
    assert current(p, inst)["overlay"] == value


def test_with_evidence_of_the_resolution_it_clears_and_the_history_shows_what_was_given(p):
    define(p)
    inst = started(p)
    flagged = json.loads(
        overlay(p, inst, "exception", "Patient match in doubt").content)["instance"]
    r = overlay(p, flagged, "clear", "Match confirmed at the desk", evidence=RESOLVED)
    assert r.status_code == 200 and json.loads(r.content)["instance"]["overlay"] == "clear"
    last = fetch(p, inst)["steps"][-1]
    assert last["kind"] == "overlay" and last["evidence"] == RESOLVED


def test_marking_something_at_risk_or_clearing_an_at_risk_needs_no_evidence(p):
    define(p)
    inst = started(p)
    risky = json.loads(overlay(p, inst, "at_risk").content)["instance"]
    assert overlay(p, risky, "clear", "The partner answered").status_code == 200
    flagged = json.loads(
        overlay(p, current(p, inst), "exception", "Needs a look").content)["instance"]
    assert flagged["overlay"] == "exception"                    # raising one needs none either


# ------------------------------------------ the context comes from the login, not the body (T09)
def test_a_request_that_names_another_customer_or_cell_still_acts_only_in_its_own(p):
    define(p)
    r = post(p, "staff/workflows", {
        "definition": "visit-flow", "subject": "subject-0001", "owner": p.desk,
        "evidence": ORIGIN, "tenant_id": "00000000-0000-0000-0000-000000000000",
        "cell_id": "ZZ-99", "organization": "someone-elses-org", "tenant": "other"}, "ops")
    assert r.status_code == 201
    made = json.loads(r.content)["instance"]
    assert made["organization_ref"] == p.clinic.org
    assert len(json.loads(p.get("staff/workflows", subject="boss").content)["instances"]) == 1
