import datetime as dt
import json

import pytest

from foundation.timeutil import utc_now
from modules.trust import api as trust
from tests.test_api import API
from tests.test_platform import Platform
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

QUIET = {"open_incidents": 0, "high_incidents": 0, "suspensions_waiting": 0, "latest_release": None}


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def platform():
    world = Platform()
    world.staff("sam", "R11")
    world.staff("tess", "R11")
    return world


def post(world, path, body, subject="sam"):
    return world.client.post(API + path, json.dumps(body), content_type="application/json",
                             **world.header(world.tenant_public_id, subject))


def work(world, subject="sam"):
    r = world.overview(subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["work"]


def incident(world, title="Mail delayed", severity="medium"):
    r = post(world, "platform/incidents",
             {"title": title, "severity": severity, "happened_on": utc_now().date().isoformat()})
    assert r.status_code == 201, r.content
    return json.loads(r.content)["incident"]["id"]


def release(world, version, days_ago=0, summary="Reports"):
    day = (utc_now() - dt.timedelta(days=days_ago)).date().isoformat()
    r = post(world, "platform/releases", {
        "version": version, "summary": summary, "released_on": day, "details": "private details"})
    assert r.status_code == 201, r.content


def ask(world, customer, action="suspend", reason="Unpaid", subject="sam"):
    r = post(world, f"platform/customers/{customer.tenant_public_id}/suspension",
             {"action": action, "reason": reason}, subject)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["id"]


def test_a_quiet_platform_has_nothing_open(platform):
    assert work(platform) == QUIET


def test_open_incidents_are_counted_and_the_high_ones_apart(platform):
    incident(platform, severity="low")
    incident(platform, severity="medium")
    incident(platform, severity="high")
    incident(platform, severity="high")
    assert work(platform) == {**QUIET, "open_incidents": 4, "high_incidents": 2}


def test_a_closed_incident_is_no_longer_counted(platform):
    keep = incident(platform, severity="high")
    done = incident(platform, severity="high")
    post(platform, f"platform/incidents/{done}/resolve", {"resolution": "fixed"})
    shown = work(platform)
    assert shown["open_incidents"] == 1 and shown["high_incidents"] == 1
    post(platform, f"platform/incidents/{keep}/resolve", {"resolution": "fixed"})
    assert work(platform) == QUIET


def test_only_requests_still_waiting_for_a_second_person_are_counted(platform):
    first, second, third, fourth = World(), World(), World(), World()
    ask(platform, first)                                              # waiting
    approved = ask(platform, second)
    post(platform, f"platform/suspensions/{approved}/approve", {}, "tess")
    rejected = ask(platform, third)
    post(platform, f"platform/suspensions/{rejected}/reject", {}, "tess")
    cancelled = ask(platform, fourth)
    post(platform, f"platform/suspensions/{cancelled}/cancel", {})
    assert work(platform)["suspensions_waiting"] == 1


def test_the_latest_release_is_the_newest_date_then_the_newest_entry(platform):
    release(platform, "1.0", days_ago=5)
    release(platform, "1.1", days_ago=1)
    release(platform, "0.9", days_ago=9)
    latest = work(platform)["latest_release"]
    yesterday = (utc_now() - dt.timedelta(days=1)).date().isoformat()
    assert latest == {"version": "1.1", "released_on": yesterday}
    release(platform, "1.2", days_ago=1)                              # same day, entered later
    assert work(platform)["latest_release"]["version"] == "1.2"


def test_only_counts_and_a_version_are_shown_never_the_text_people_wrote(platform):
    incident(platform, title="SECRET-TITLE", severity="high")
    ask(platform, World(), reason="SECRET-REASON")
    release(platform, "7.7", summary="SECRET-SUMMARY")
    text = json.dumps(work(platform))
    for hidden in ("SECRET-TITLE", "SECRET-REASON", "SECRET-SUMMARY", "private details"):
        assert hidden not in text
    assert set(work(platform)) == set(QUIET)


def test_the_safety_officer_sees_the_same_numbers(platform):
    platform.staff("iyer", "R12")
    incident(platform, severity="high")
    ask(platform, World())
    release(platform, "2.0")
    assert work(platform, "iyer") == work(platform)
    assert work(platform)["open_incidents"] == 1


def test_another_platform_tenant_has_its_own_numbers(platform):
    incident(platform, severity="high")
    other = Platform()
    other.staff("zed", "R11")
    assert work(other, "zed") == QUIET


def test_the_numbers_stay_behind_the_same_permission_as_the_rest_of_the_overview(platform):
    incident(platform)
    platform.staff("other", "R10", kind=trust.ActorType.WORKLOAD)
    assert platform.overview("other").status_code == 403
    assert platform.client.get(API + "platform/overview").status_code == 401


def test_the_rest_of_the_overview_is_unchanged(platform):
    body = json.loads(platform.overview("sam").content)
    assert {"cell", "generated_at", "outbox", "mail", "work", "tenants"} <= set(body)
    assert any(t["name"] == platform.tenant_name for t in body["tenants"])
