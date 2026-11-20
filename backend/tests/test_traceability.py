"""The requirements matrix (GHE-19; DEV-138): recorded readings of the specifications' acceptance
tests, with one honesty rule (a requirement cannot be called met without naming a runnable test)."""
import json

import pytest
from django.core.management import call_command

from foundation.cell_context import tenant_transaction
from modules.evidence.models import AuditEvent
from modules.traceability import api as traceability
from modules.traceability.models import Requirement
from tests.test_packs import call
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def world():
    w = Platform()
    w.staff("sam", "R11")
    w.staff("iyer", "R12")
    return w


def row(req_id="WF-T01", status="not_built", evidence="Nothing yet.", source="GHE-12", **over):
    return {"source": source, "req_id": req_id, "group": "Activation", "title": "A requirement",
            "status": status, "evidence": evidence, **over}


def push(w, rows, who="sam"):
    return call(w, "POST", "platform/traceability/import", {"rows": rows}, who)


def listing(w, query="", who="sam"):
    r = call(w, "GET", "platform/traceability" + query, who=who)
    assert r.status_code == 200, r.content
    return json.loads(r.content)


# ------------------------------------------------------------------ the honesty rule
def test_a_requirement_cannot_be_called_met_without_naming_a_test(world):
    for evidence in ("", "It works.", "see test_something", "tested in test_x"):
        assert push(world, [row(status="met", evidence=evidence)]).status_code == 422
    assert listing(world)["requirements"] == []
    ok = push(world, [row(status="met", evidence="test_orchestration.py: proves it")])
    assert ok.status_code == 200 and ok.json() == {"added": 1, "changed": 0, "unchanged": 0}


def test_partly_and_not_built_need_no_test(world):
    rows = [row("A-1", "partly", "Half done"), row("A-2", "not_built", "")]
    assert push(world, rows).status_code == 200


@pytest.mark.parametrize("bad", [
    {"source": "GHE12"}, {"source": "ghe-12"}, {"req_id": ""}, {"req_id": "has space"},
    {"status": "done"}, {"title": "x"}, {"title": "y" * 501}, {"evidence": "z" * 1001},
    {"group": "g" * 81}, {"title": "bad\x00char"},
])
def test_malformed_rows_are_refused(world, bad):
    assert push(world, [row(**bad)]).status_code == 422


def test_a_batch_is_all_or_nothing_and_has_no_repeats_and_a_size_limit(world):
    assert push(world, [row("A-1"), row("A-2", status="nonsense")]).status_code == 422
    assert listing(world)["requirements"] == []
    assert push(world, [row("A-1"), row("A-1")]).status_code == 422
    assert push(world, []).status_code == 422
    assert push(world, "rows").status_code == 422
    assert push(world, [row(f"R-{i}") for i in range(501)]).status_code == 422


# ------------------------------------------------------------------ importing and updating
def test_importing_again_adds_changes_and_leaves_alone(world):
    push(world, [row("A-1"), row("A-2")])
    again = push(world, [row("A-1"), row("A-2", "partly", "Half"), row("A-3")]).json()
    assert again == {"added": 1, "changed": 1, "unchanged": 1}
    assert len(listing(world)["requirements"]) == 3


def test_a_requirement_can_be_updated_and_only_a_change_is_audited(world):
    push(world, [row("A-1")])
    rid = listing(world)["requirements"][0]["public_id"]
    url = f"platform/traceability/{rid}/update"
    good = {"status": "met", "evidence": "test_packs.py: covers it"}
    r = call(world, "POST", url, good, "sam")
    assert r.status_code == 200 and json.loads(r.content)["requirement"]["status"] == "met"
    call(world, "POST", url, good, "sam")
    assert call(world, "POST", url, {"status": "met", "evidence": "nothing"}, "sam"
                ).status_code == 422
    with tenant_transaction(world.ctx):
        actions = [e.action for e in AuditEvent.objects.filter(
            action__startswith="traceability.").order_by("chain_seq")]
    assert actions == ["traceability.imported", "traceability.updated"]


def test_updating_an_unknown_requirement_is_not_found(world):
    r = call(world, "POST", "platform/traceability/nonexistent-id-00000/update",
             {"status": "partly", "evidence": ""}, "sam")
    assert r.status_code == 404


def test_nothing_is_ever_deleted_by_an_import(world):
    push(world, [row("A-1"), row("A-2")])
    push(world, [row("A-1")])
    assert len(listing(world)["requirements"]) == 2


# ------------------------------------------------------------------ who may do what
def test_only_support_writes_and_only_platform_staff_read(world):
    assert push(world, [row()], "iyer").status_code == 403
    push(world, [row()])
    assert len(listing(world, who="iyer")["requirements"]) == 1
    assert listing(world, who="iyer")["can_write"] is False
    from tests.test_clinician import Practice
    p = Practice()
    for who in ("ops", "doc", "pat"):
        assert p.get("platform/traceability", subject=who).status_code == 403
        assert p.post("platform/traceability/import", {"rows": [row()]},
                      subject=who).status_code == 403


# ------------------------------------------------------------------ the summary and filters
def test_the_summary_counts_each_standing_per_specification(world):
    push(world, [row("A-1", "met", "test_a.py: x"), row("A-2", "partly", "x"), row("A-3"),
                 row("B-1", "met", "test_b.py: y", source="GHE-13")])
    s = listing(world)["summary"]
    assert s["GHE-12"] == {"total": 3, "met": 1, "partly": 1, "not_built": 1}
    assert s["GHE-13"] == {"total": 1, "met": 1, "partly": 0, "not_built": 0}


def test_the_list_can_be_filtered_but_the_summary_stays_whole(world):
    push(world, [row("A-1", "met", "test_a.py: x"), row("A-2", "partly", "x"), row("A-3")])
    got = listing(world, "?status=partly")
    assert [r["req_id"] for r in got["requirements"]] == ["A-2"]
    assert got["summary"]["GHE-12"]["total"] == 3
    assert [r["req_id"] for r in listing(world, "?source=GHE-13")["requirements"]] == []
    assert call(world, "GET", "platform/traceability?status=bogus", who="sam").status_code == 422


# ------------------------------------------------------------------ the recorded readings
def test_the_recorded_readings_load_and_obey_the_same_rule(world):
    rows = traceability.seed_rows()
    assert len(rows) == 398
    assert all(r["status"] != "met" or "test_" in r["evidence"] for r in rows)
    r = call(world, "POST", "platform/traceability/import", {"seed": True}, "sam")
    assert r.status_code == 200 and r.json()["added"] == 398
    s = listing(world)["summary"]
    got = [s[k]["total"] for k in sorted(s)]
    assert sorted(s) == ["GHE-04", "GHE-06", "GHE-08", "GHE-12", "GHE-13", "GHE-16", "GHE-18"]
    assert got == [36, 40, 54, 60, 64, 64, 80]
    again = call(world, "POST", "platform/traceability/import", {"seed": True}, "sam")
    assert again.json() == {"added": 0, "changed": 0, "unchanged": 398}


def test_the_command_loads_every_platform_tenant_and_is_safe_to_repeat(world):
    call_command("load_traceability")
    call_command("load_traceability")
    with tenant_transaction(world.ctx):
        assert Requirement.objects.count() == 398
