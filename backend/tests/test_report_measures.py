"""Report measures (DEV-389, GHE-10): every number has an approved definition, small numbers are
suppressed (with differencing protection), unapproved measures are withheld, and each run is
recorded so it can be reproduced. Mostly what must NOT be shown."""
import json

import pytest

from foundation import measures, template_policy
from foundation.cell_context import tenant_transaction
from foundation.release_check import release_problems
from modules.evidence.models import AuditEvent
from tests.test_clinician import Practice
from tests.test_platform import Platform
from tests.test_release_check import good

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


# ------------------------------------------------------------------ small numbers (no database)
def test_counts_of_one_to_four_are_suppressed_and_zero_is_shown():
    got, paths = measures.protect({"waiting_list": {"joined": 3, "booked": 0, "left": 12,
                                                    "waiting_now": 5}})
    assert got["waiting_list"] == {"joined": None, "booked": 0, "left": 12, "waiting_now": 5}
    assert paths == ["waiting_list.joined"]


def test_one_hidden_number_in_a_group_hides_a_second_so_it_cannot_be_subtracted():
    report = {"bookings": {"total": 30, "status": {"requested": 2, "confirmed": 20,
                                                   "cancelled": 8}}}
    got, paths = measures.protect(report)
    status = got["bookings"]["status"]
    assert status == {"requested": None, "confirmed": 20, "cancelled": None}   # 8 hidden too
    assert {"bookings.status.requested", "bookings.status.cancelled"} <= set(paths)


def test_a_rate_or_an_average_from_fewer_than_five_cases_is_withheld():
    report = {"bookings": {"by_doctor": [{"provider_ref": "d1", "attended": 2, "no_show": 1,
                                          "not_recorded": 9, "no_show_rate": 0.33}],
                           "hours_to_confirm": {"count": 3, "median": 1.0, "average": 1.2}},
              "messages": {"started": 40, "closed": 30, "waiting_for_clinic": 0,
                           "hours_to_first_reply": {"count": 12, "median": 2.0, "average": 3.0}}}
    got, _ = measures.protect(report)
    doctor = got["bookings"]["by_doctor"][0]
    assert doctor["no_show_rate"] is None and doctor["attended"] is None
    assert got["bookings"]["hours_to_confirm"] == {"count": None, "median": None, "average": None}
    assert got["messages"]["hours_to_first_reply"]["median"] == 2.0      # 12 cases: shown


def test_large_numbers_are_untouched():
    report = {"referrals": {"received": 50, "issued": 10, "accepted": 30, "needs_info": 5,
                            "declined": 5}}
    got, paths = measures.protect(report)
    assert got == report and paths == []


def test_every_measure_has_a_definition_and_a_version():
    assert len(measures.MEASURES) == 10
    for m in measures.MEASURES.values():
        assert m.numerator and m.denominator and m.exclusions and len(m.version) == 16
    changed = measures.Measure("x", "X", "a", "b", "c", ("y",), "code v2")
    same_but_older_code = measures.Measure("x", "X", "a", "b", "c", ("y",), "code v1")
    assert changed.version != same_but_older_code.version          # code changes are versions


# ------------------------------------------------------------------ the clinic's report
def report_of(p):
    r = p.get("staff/reports?days=30", subject="ops")
    assert r.status_code == 200, r.content
    return json.loads(r.content)


def test_the_report_carries_its_measures_rule_and_evidence(settings):
    settings.ZOIKO_MEASURE_GATING = "report"
    p = Practice()
    got = report_of(p)
    assert {m["key"] for m in got["measures"]} == set(measures.MEASURES)
    assert got["small_cell_k"] == 5 and len(got["evidence"]) == 32 and got["withheld"] == []
    with tenant_transaction(p.clinic.ctx):
        run = AuditEvent.objects.filter(action="report.run",
                                        tenant_id=p.clinic.ctx.tenant_id).get()
    assert run.details["result"] == got["evidence"] and run.details["small_cell_k"] == 5
    assert set(run.details) == {"days", "from_day", "to_day", "small_cell_k", "suppressed",
                                "withheld", "measures", "result"}             # never the numbers


def test_unapproved_measures_are_withheld_when_enforced(settings):
    settings.ZOIKO_MEASURE_GATING = "enforce"
    got = report_of(Practice())
    assert set(got["withheld"]) == set(measures.MEASURES)
    assert got["referrals"] is None and got["waiting_list"] is None
    assert got["bookings"]["total"] is None and got["forecast"] is None


def test_an_approved_measure_is_shown(settings):
    settings.ZOIKO_MEASURE_GATING = "enforce"
    zoiko = Platform()
    zoiko.staff("iyer", "R12")
    zoiko.staff("sam", "R11")
    with tenant_transaction(zoiko.ctx):
        template_policy.approve(channel="measure", key="waiting_list_flow", as_role="clinical",
                                actor_ref="iyer-ref")
        template_policy.approve(channel="measure", key="waiting_list_flow", as_role="privacy",
                                actor_ref="sam-ref")
    got = report_of(Practice())
    assert "waiting_list_flow" not in got["withheld"]
    assert got["waiting_list"] == {"joined": 0, "booked": 0, "left": 0, "waiting_now": 0}
    assert next(m for m in got["measures"] if m["key"] == "waiting_list_flow")["approved"]


def test_measures_are_approved_on_the_same_screen_as_message_templates():
    zoiko = Platform()
    zoiko.staff("sam", "R11")
    r = zoiko.client.get("/api/v1/platform/templates",
                         **zoiko.header(zoiko.tenant_public_id, "sam"))
    rows = json.loads(r.content)["templates"]
    found = [t for t in rows if t["channel"] == "measure"]
    assert len(found) == 10 and all("Out of:" in t["wording"] for t in found)


@pytest.mark.parametrize("value", ["report", "off", ""])
def test_a_real_server_must_enforce(value):
    assert any("ZOIKO_MEASURE_GATING" in x
               for x in release_problems(good(ZOIKO_MEASURE_GATING=value)))
    assert release_problems(good(ZOIKO_MEASURE_GATING="enforce")) == []


@pytest.mark.parametrize("k", [1, 4, "5", None])
def test_a_real_server_never_shows_counts_under_five(k):
    assert any("REPORT_SMALL_CELL_K" in x for x in release_problems(good(REPORT_SMALL_CELL_K=k)))
    assert release_problems(good(REPORT_SMALL_CELL_K=5)) == []
