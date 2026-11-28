import datetime as dt
import json

import pytest

from foundation.cell_context import tenant_transaction
from foundation.timeutil import UTC, FixedClock
from modules.policy import api as policy
from modules.policy.models import AccessDecision
from tests.test_clinician import Practice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

NOW = dt.datetime(2027, 9, 6, 12, 0, tzinfo=UTC)
CLOCK = FixedClock(NOW)


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def p():
    return Practice()


def decide(p, actor, allowed, days_ago=0, hour=9):
    when = (NOW - dt.timedelta(days=days_ago)).replace(hour=hour, minute=0)
    with tenant_transaction(p.clinic.ctx):
        AccessDecision.objects.create(
            actor_ref=actor, operation="read:person", purpose_code="TREATMENT", allowed=allowed,
            reason_code="x", policy_version="v", request_id="r", created_at=when)


def summary(p, **kw):
    with tenant_transaction(p.clinic.ctx):
        return policy.denial_summary(clock=CLOCK, **kw)


def test_every_day_is_listed_even_a_quiet_one_and_today_is_last(p):
    decide(p, "a", False, days_ago=0)
    days = summary(p, days=7)["by_day"]
    assert len(days) == 7
    assert days[-1] == {"day": "2027-09-06", "denied": 1, "total": 1}
    assert days[0]["day"] == "2027-08-31"
    assert all(d["denied"] == 0 and d["total"] == 0 for d in days[:-1])


def test_denied_and_all_checks_are_counted_per_day(p):
    decide(p, "a", False, days_ago=1)
    decide(p, "a", False, days_ago=1)
    decide(p, "b", True, days_ago=1)
    yesterday = [d for d in summary(p)["by_day"] if d["day"] == "2027-09-05"][0]
    assert yesterday == {"day": "2027-09-05", "denied": 2, "total": 3}


def test_a_check_older_than_the_window_is_left_out(p):
    decide(p, "a", False, days_ago=7)                              # the 8th day back: outside 7
    s = summary(p, days=7)
    assert s["by_person"] == [] and sum(d["total"] for d in s["by_day"]) == 0
    assert summary(p, days=8)["by_person"] == [{"actor": "a", "denied": 1, "total": 1}]


def test_the_first_day_of_the_window_is_included_from_midnight(p):
    decide(p, "a", False, days_ago=6, hour=0)
    assert summary(p, days=7)["by_day"][0] == {"day": "2027-08-31", "denied": 1, "total": 1}


def test_people_are_ordered_by_refusals_and_show_all_their_checks(p):
    for _ in range(3):
        decide(p, "zed", False)                       # last alphabetically, most refused
    decide(p, "zed", True)
    decide(p, "amy", False)
    assert summary(p)["by_person"] == [
        {"actor": "zed", "denied": 3, "total": 4}, {"actor": "amy", "denied": 1, "total": 1}]


def test_someone_who_was_never_refused_is_not_listed(p):
    decide(p, "fine", True)
    assert summary(p)["by_person"] == []


def test_only_the_top_people_are_returned(p):
    for n in range(5):
        decide(p, f"user{n}", False)
    assert len(summary(p, top=3)["by_person"]) == 3


def test_another_customers_checks_are_not_counted(p):
    decide(p, "a", False)
    other = Practice()
    with tenant_transaction(other.clinic.ctx):
        s = policy.denial_summary(clock=CLOCK)
    assert s["by_person"] == [] and sum(d["total"] for d in s["by_day"]) == 0


@pytest.mark.parametrize("asked,got", [(0, 1), (-5, 1), (7, 7), (31, 31), (500, 31)])
def test_the_number_of_days_is_kept_between_one_and_thirty_one(p, asked, got):
    assert len(summary(p, days=asked)["by_day"]) == got


def test_the_compliance_page_carries_it_for_staff(p):
    r = p.get("staff/compliance", subject="ops")
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["refused"]["days"] == 7 and len(body["refused"]["by_day"]) == 7


def test_a_patient_cannot_read_it(p):
    assert p.get("staff/compliance", subject="pat").status_code == 403
