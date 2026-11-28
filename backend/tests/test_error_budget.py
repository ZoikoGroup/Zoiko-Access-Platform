"""The 30-day error budget, burn-rate alerts with runbooks, and the release hold with its safety
override (DEV-388, GHE-18). Mostly refusals: no override without a used-up budget, by the wrong
officer, for too long, without a reason, or after it expired."""
import datetime as dt
import json
from pathlib import Path

import pytest
from django.test import Client

from foundation import error_budget
from foundation.models import SloMinute
from foundation.release_check import release_problems
from foundation.timeutil import FixedClock, utc_now
from tests import test_readiness
from tests.test_readiness import call, release
from tests.test_release_check import good

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

world = test_readiness.world      # a platform whose other go-live holds are all settled


@pytest.fixture(autouse=True)
def recording(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    settings.ZOIKO_SLO_RECORDING = True


def minutes(total, errors=0, slow=0, ago_minutes=60):
    """Requests spread over one minute `ago_minutes` ago."""
    at = (utc_now() - dt.timedelta(minutes=ago_minutes)).replace(second=0, microsecond=0)
    SloMinute.objects.create(minute=at, total=total, server_errors=errors, fast=total - slow)


# ------------------------------------------------------------------ counting
def test_every_request_is_counted_in_its_minute_and_health_checks_are_not():
    client = Client()
    client.get("/api/v1/version")
    client.get("/api/v1/version")
    client.get("/healthz")
    row = SloMinute.objects.get()
    assert (row.total, row.server_errors) == (2, 0)


def test_a_server_error_and_a_slow_answer_are_counted():
    clock = FixedClock(utc_now())
    error_budget.count("api/v1/bookings", 503, 0.1, clock=clock)
    error_budget.count("api/v1/bookings", 200, 2.0, clock=clock)
    error_budget.count("api/v1/bookings", 404, 0.1, clock=clock)    # a 4xx is a valid answer
    row = SloMinute.objects.get()
    assert (row.total, row.server_errors, row.fast) == (3, 1, 2)


def test_counting_can_be_off_but_never_on_a_real_server(settings):
    settings.ZOIKO_SLO_RECORDING = False
    error_budget.count("api/v1/bookings", 500, 0.1)
    assert SloMinute.objects.count() == 0
    assert any("ZOIKO_SLO_RECORDING" in p
               for p in release_problems(good(ZOIKO_SLO_RECORDING=False)))
    assert release_problems(good(ZOIKO_SLO_RECORDING=True)) == []


# ------------------------------------------------------------------ the budget
def test_a_healthy_budget():
    minutes(10_000, errors=2, ago_minutes=60 * 24 * 3)
    got = error_budget.summary()["budgets"]["availability"]
    assert got["state"] == "healthy" and got["allowed_failures"] == 10.0 and got["used"] == 0.2


def test_three_quarters_used_is_a_warning_and_all_of_it_is_exhausted():
    minutes(10_000, errors=8, ago_minutes=60 * 24 * 3)
    assert error_budget.summary()["budgets"]["availability"]["state"] == "warning"
    minutes(3, errors=3, ago_minutes=60 * 24 * 2)      # 11 failures, about 10 allowed
    found = error_budget.summary()
    assert found["budgets"]["availability"]["state"] == "exhausted" and found["exhausted"]
    assert {"alert": "availability_exhausted",
            "runbook": "docs/runbooks/08-error-budget-exhausted.md"} in found["alerts"]


def test_slow_answers_use_the_latency_budget():
    minutes(10_000, slow=600, ago_minutes=60 * 24 * 3)     # 6% slow: the target allows 5%
    found = error_budget.summary()["budgets"]
    assert found["latency"]["state"] == "exhausted" and found["availability"]["state"] == "healthy"


def test_with_little_traffic_nothing_is_judged():
    minutes(100, errors=50)
    found = error_budget.summary()
    assert found["budgets"]["availability"]["state"] == "not_enough_traffic"
    assert not found["exhausted"]


def test_old_failures_age_out_of_the_window():
    minutes(10_000, errors=50, ago_minutes=60 * 24 * 31)
    minutes(10_000, errors=0, ago_minutes=60)
    assert error_budget.summary()["budgets"]["availability"]["state"] == "healthy"


def test_a_fast_burn_raises_an_alert_with_its_runbook():
    minutes(2000, errors=40, ago_minutes=30)               # 2% failing: 20x the allowed rate
    found = error_budget.summary()
    assert {"alert": "availability_fast_burn",
            "runbook": "docs/runbooks/01-server-errors.md"} in found["alerts"]
    assert found["burn"]["last_hour"]["availability"] >= 14.4


def test_minutes_older_than_35_days_are_forgotten():
    minutes(10, ago_minutes=60 * 24 * 36)
    minutes(10, ago_minutes=60)
    assert error_budget.forget_old() == 1 and SloMinute.objects.count() == 1


def test_every_runbook_named_exists():
    root = Path(__file__).resolve().parents[2]
    if not (root / "docs").is_dir():
        pytest.skip("the repository root is not visible from here")
    names = set(error_budget.RUNBOOKS.values())
    from api.error_budget import OTHER_RUNBOOKS
    for name in names | set(OTHER_RUNBOOKS):
        assert (root / name).is_file(), name


# ------------------------------------------------------------------ the screen
def test_zoiko_staff_see_the_budget_and_customers_do_not(world):
    minutes(10_000, errors=1)
    data = json.loads(call(world, "GET", "platform/error-budget", who="sam").content)
    assert data["budgets"]["availability"]["state"] == "healthy" and data["runbooks"]
    from tests.test_clinician import Practice
    assert Practice().get("platform/error-budget", subject="ops").status_code == 403


# ------------------------------------------------------------------ the release hold
def exhaust():
    minutes(10_000, errors=20, ago_minutes=60 * 24 * 3)


def blockers(w, rel):
    r = call(w, "GET", f"platform/readiness/{rel['public_id']}", who="sam")
    return json.loads(r.content)["release"]["blockers"]


def override(w, rel, who="iyer", reason="A fix for the cause of the errors", days=3):
    return call(w, "POST", f"platform/readiness/{rel['public_id']}/budget-override",
                {"reason": reason, "days": days}, who)


def test_a_used_up_budget_holds_every_release(world):
    rel = release(world)
    assert "g3.error_budget:missing" not in blockers(world, rel)
    exhaust()
    assert "g3.error_budget:missing" in blockers(world, rel)


def test_a_safety_officer_may_override_it_for_one_release_with_a_reason(world):
    rel = release(world)
    other = release(world, ref="rel-2026-11-2")
    exhaust()
    r = override(world, rel)
    assert r.status_code == 200, r.content
    assert "g3.error_budget:missing" not in blockers(world, rel)
    assert "g3.error_budget:missing" in blockers(world, other)          # only that release


@pytest.mark.parametrize("change, status", [
    ({"who": "sam"}, 403), ({"reason": "fix"}, 422), ({"days": 8}, 422), ({"days": 0}, 422),
    ({"days": True}, 422),
])
def test_an_override_is_refused_without_its_conditions(world, change, status):
    rel = release(world)
    exhaust()
    assert override(world, rel, **change).status_code == status


def test_no_override_while_the_budget_is_not_used_up(world):
    rel = release(world)
    r = override(world, rel)
    assert r.status_code == 409 and json.loads(r.content)["fields"] == ["budget_not_exhausted"]


def test_an_override_ends_after_its_days(world, monkeypatch):
    rel = release(world)
    exhaust()
    override(world, rel, days=1)
    from modules.readiness import services
    later = FixedClock(utc_now() + dt.timedelta(days=2))
    real = services.utc_now
    monkeypatch.setattr(services, "utc_now", lambda clock=None: later.now() if clock is None
                        else real(clock))
    assert "g3.error_budget:missing" in blockers(world, rel)
