"""Risk controls of the hazard register, tied to the tests that prove them (DEV-382): proposing,
the independent check, withdrawing, what acceptance now needs, re-opening a signed-off hazard when
its controls change, and the hazard -> control -> test trace. Mostly what must be REFUSED."""
import json

import pytest

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from tests.test_hazards import ASSESSMENT, CONTROL, get, hazard, post, step
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

REAL_TEST = "tests/test_hazard_controls.py::test_a_control_must_name_a_test_that_exists"


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.ZOIKO_BUILD = "build-382"


@pytest.fixture
def platform():
    world = Platform()
    world.staff("sam", "R11")
    world.staff("iyer", "R12")
    world.staff("rao", "R12")
    return world


def propose(world, ref="H01", subject="iyer", **over):
    return post(world, f"platform/hazards/{hazard(world, ref)['id']}/controls",
                {**CONTROL, "test_ref": REAL_TEST, **over}, subject)


def control_step(world, control_id, name, subject="rao", **body):
    return post(world, f"platform/hazard-controls/{control_id}/{name}", body, subject)


def proposed(world, ref="H01") -> str:
    r = propose(world, ref)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["control"]["id"]


def h01(data):
    return next(h for h in data["hazards"] if h["ref_no"] == "H01")


def trace(world, subject="iyer"):
    r = get(world, "platform/hazard-trace", subject)
    assert r.status_code == 200
    return json.loads(r.content)


# ------------------------------------------------------------------ proposing
def test_a_control_is_numbered_and_names_where_it_lives_and_its_test(platform):
    first = json.loads(propose(platform).content)["control"]
    second = json.loads(propose(platform, "H02").content)["control"]
    assert (first["control_no"], second["control_no"]) == ("C-001", "C-002")
    assert first["state"] == "proposed" and first["test"] == "found"
    assert first["test_ref"] == REAL_TEST and first["kind"] == "preventive"


def test_a_control_must_name_a_test_that_exists(platform):
    for bad in ("", "tests/test_hazards.py", "tests/test_nothing_here.py::test_x",
                "tests/test_hazards.py::test_no_such_test", "../etc/passwd::test_x",
                "backend/tests/test_hazards.py::test_x"):
        r = propose(platform, test_ref=bad)
        assert r.status_code == 422 and json.loads(r.content)["fields"] == ["test_ref"], bad


@pytest.mark.parametrize("change, field", [
    ({"kind": "hope"}, "kind"), ({"description": "short"}, "description"),
    ({"implementation": ""}, "implementation"),
])
def test_an_unusable_control_is_refused(platform, change, field):
    r = propose(platform, **change)
    assert r.status_code == 422 and field in json.loads(r.content)["fields"]


def test_support_may_read_controls_but_not_add_or_check_them(platform):
    cid = proposed(platform)
    assert propose(platform, subject="sam").status_code == 403
    assert control_step(platform, cid, "verify", "sam", note="Looked at it").status_code == 403
    r = get(platform, f"platform/hazards/{hazard(platform)['id']}/controls", "sam")
    assert r.status_code == 200 and json.loads(r.content)["can_decide"] is False


def test_a_retired_hazard_takes_no_new_controls(platform):
    step(platform, "retire", subject="iyer", reason="No longer applies")
    assert propose(platform).status_code == 409


# ------------------------------------------------------------------ the independent check
def test_a_control_is_checked_by_someone_else_and_signed_with_the_build(platform):
    cid = proposed(platform)
    assert control_step(platform, cid, "verify", "iyer", note="My own").status_code == 403
    assert control_step(platform, cid, "verify", note="").status_code == 422   # say what
    r = control_step(platform, cid, "verify", note="Ran the named test on this build: passed")
    done = json.loads(r.content)["control"]
    assert r.status_code == 200 and done["state"] == "verified"
    assert done["verified_build"] == "build-382" and done["verified_by"]
    assert control_step(platform, cid, "verify", note="Again please").status_code == 409


def test_a_withdrawn_control_stays_on_the_record(platform):
    cid = proposed(platform)
    assert control_step(platform, cid, "withdraw", reason="").status_code == 422
    r = control_step(platform, cid, "withdraw", reason="Replaced by a better check")
    assert json.loads(r.content)["control"]["state"] == "withdrawn"
    assert control_step(platform, cid, "verify", note="Too late").status_code == 409
    rows = json.loads(get(platform, f"platform/hazards/{hazard(platform)['id']}/controls").content)
    assert [c["state"] for c in rows["controls"]] == ["withdrawn"]


# ------------------------------------------------------------------ what acceptance needs
def _verified_hazard(platform, with_control=True):
    post(platform, f"platform/hazards/{hazard(platform)['id']}/assess", ASSESSMENT, "iyer")
    if with_control:
        cid = proposed(platform)
        control_step(platform, cid, "verify", note="Ran the named test on this build: passed")
    step(platform, "review", subject="rao", verdict="verified")


def test_no_acceptance_without_a_verified_control(platform):
    _verified_hazard(platform, with_control=False)
    r = step(platform, "accept", subject="rao")
    assert r.status_code == 422 and json.loads(r.content)["fields"] == ["controls"]
    assert hazard(platform)["status"] == "VERIFIED"


def test_no_acceptance_while_a_control_still_waits_for_its_check(platform):
    _verified_hazard(platform)
    r = propose(platform, subject="rao", kind="detection",   # one more, still unchecked
                description="An alert when a booking has no patient check")
    assert r.status_code == 201
    step(platform, "review", subject="rao", verdict="verified")    # it reopened: review again
    r = step(platform, "accept", subject="rao")
    assert r.status_code == 422 and json.loads(r.content)["fields"] == ["controls"]


def test_with_a_verified_control_the_remaining_risk_can_be_accepted(platform):
    _verified_hazard(platform)
    assert step(platform, "accept", subject="rao").status_code == 200
    assert hazard(platform)["status"] == "ACCEPTED"


def test_changing_the_controls_of_an_accepted_hazard_reopens_it(platform):
    _verified_hazard(platform)
    step(platform, "accept", subject="rao")
    proposed(platform)                              # a new control: the old sign-off is void
    h = hazard(platform)
    assert h["status"] == "OPEN" and h["reviewer"] == ""
    history = json.loads(get(platform, f"platform/hazards/{h['id']}/history").content)
    assert history["steps"][-1]["action"] == "controls_changed"


def test_withdrawing_a_control_of_a_verified_hazard_reopens_it(platform):
    _verified_hazard(platform)
    cid = h01(trace(platform))["controls"][0]["id"]
    control_step(platform, cid, "withdraw", reason="It did not cover partner results")
    assert hazard(platform)["status"] == "OPEN"


# ------------------------------------------------------------------ the trace
def test_the_trace_shows_every_hazard_its_controls_and_the_gaps(platform):
    _verified_hazard(platform)
    proposed(platform, "H02")
    data = trace(platform)
    rows = {h["ref_no"]: h for h in data["hazards"]}
    assert len(rows) == 36
    assert rows["H01"]["verified_controls"] == 1 and rows["H01"]["controls"][0]["test"] == "found"
    assert rows["H02"]["controls"][0]["state"] == "proposed"
    gaps = data["gaps"]
    assert gaps["without_verified_control"] == 35 and gaps["controls_waiting"] == 1
    assert gaps["tests_missing"] == 0 and gaps["accepted_without_verified_control"] == 0


def test_a_test_that_disappears_shows_in_the_trace(platform, monkeypatch):
    proposed(platform)
    from modules.safety import controls
    monkeypatch.setattr(controls, "test_status", lambda ref: controls.MISSING)
    data = trace(platform)
    assert data["gaps"]["tests_missing"] == 1
    cid = h01(data)["controls"][0]["id"]
    r = control_step(platform, cid, "verify", note="Ran the named test on this build")
    assert r.status_code == 422 and json.loads(r.content)["fields"] == ["test_ref"]


def test_every_control_step_is_audited(platform):
    cid = proposed(platform)
    control_step(platform, cid, "verify", note="Ran the named test on this build: passed")
    with tenant_transaction(platform.ctx):
        actions = set(AuditEvent.objects.filter(tenant_id=platform.ctx.tenant_id)
                      .values_list("action", flat=True))
    assert {"safety.control.propose", "safety.control.verify"} <= actions
