import datetime as dt
import json
from typing import Any

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from tests.test_api import API
from tests.test_platform import Platform
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture
def platform():
    world = Platform()
    world.staff("sam", "R11")
    return world


def today():
    return utc_now().date()


def add(world, subject="sam", **fields):
    body = {"version": "2026.11.1", "summary": "Reports and work queue",
            "released_on": today().isoformat(), **fields}
    return world.client.post(API + "platform/releases", json.dumps(body),
                             content_type="application/json",
                             **world.header(world.tenant_public_id, subject))


def listing(world, subject="sam"):
    return world.client.get(API + "platform/releases",
                            **world.header(world.tenant_public_id, subject))


def releases(world, subject="sam"):
    r = listing(world, subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["releases"]


def test_support_records_a_release_and_it_appears_in_the_log(platform):
    r = add(platform, details="Adds the Releases page.\nNo database change.")
    assert r.status_code == 201
    [row] = releases(platform)
    assert row["version"] == "2026.11.1" and row["summary"] == "Reports and work queue"
    assert row["details"] == "Adds the Releases page.\nNo database change."
    assert row["released_on"] == today().isoformat()
    assert row["recorded_by"]


def test_an_empty_log_is_an_empty_list(platform):
    assert releases(platform) == []


def test_the_newest_release_date_comes_first_then_the_newest_entry(platform):
    add(platform, version="a", released_on=(today() - dt.timedelta(days=5)).isoformat())
    add(platform, version="b", released_on=today().isoformat())
    add(platform, version="c", released_on=(today() - dt.timedelta(days=5)).isoformat())
    assert [r["version"] for r in releases(platform)] == ["b", "c", "a"]


def test_the_safety_officer_can_read_but_not_add(platform):
    platform.staff("iyer", "R12")
    add(platform)
    assert len(releases(platform, "iyer")) == 1
    assert add(platform, "iyer", version="other").status_code == 403
    assert len(releases(platform)) == 1


@pytest.mark.parametrize("role,kind", [("R10", trust.ActorType.WORKLOAD),
                                       ("R04", trust.ActorType.FACILITY_USER)])
def test_other_platform_roles_can_neither_read_nor_add(platform, role, kind):
    platform.staff("other", role, kind=kind)
    assert listing(platform, "other").status_code == 403
    assert add(platform, "other").status_code == 403
    assert releases(platform) == []


def test_a_customers_staff_cannot_use_it_even_with_a_support_role():
    customer = World()                                              # a clinic, not the platform
    with tenant_transaction(customer.ctx):
        actor = trust.register_actor(actor_type=trust.ActorType.SUPPORT, issuer="idp", subject="x")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=customer.org,
                          role_code="R11", by="system")
    client = Client()
    header: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer dev:{customer.tenant_public_id}:idp:x"}
    assert client.get(API + "platform/releases", **header).status_code == 403
    body = json.dumps({"version": "1", "summary": "s", "released_on": today().isoformat()})
    assert client.post(API + "platform/releases", body, content_type="application/json",
                       **header).status_code == 403


def test_a_version_can_be_recorded_only_once_even_with_stray_spaces(platform):
    assert add(platform, version="1.0").status_code == 201
    assert add(platform, version="1.0").status_code == 409
    assert add(platform, version="  1.0  ").status_code == 409
    assert len(releases(platform)) == 1


@pytest.mark.parametrize("fields", [
    {"version": ""}, {"version": "   "}, {"version": "v" * 41}, {"version": "a\x07b"},
    {"version": "line\nbreak"}, {"version": 12}, {"version": None},
    {"summary": ""}, {"summary": "s" * 201}, {"summary": "bad\x00text"},
    {"summary": "two\nlines"}, {"summary": ["list"]},
    {"details": "d" * 2001}, {"details": "bell\x07"}, {"details": 5},
    {"released_on": "not-a-date"}, {"released_on": ""}, {"released_on": "2026-13-40"},
    {"released_on": (today() + dt.timedelta(days=2)).isoformat()},
])
def test_bad_entries_are_refused_and_nothing_is_saved(platform, fields):
    assert add(platform, **fields).status_code == 422
    assert releases(platform) == []


def test_the_longest_allowed_entry_is_accepted(platform):
    r = add(platform, version="v" * 40, summary="s" * 200, details="d" * 2000)
    assert r.status_code == 201


def test_tomorrow_is_allowed_for_time_zones_but_not_the_day_after(platform):
    assert add(platform, version="t", released_on=(today() + dt.timedelta(days=1)).isoformat()
               ).status_code == 201
    assert add(platform, version="u", released_on=(today() + dt.timedelta(days=2)).isoformat()
               ).status_code == 422


def test_each_entry_is_audited_once_and_a_refusal_is_not(platform):
    add(platform)
    add(platform)                                                   # refused: same version
    add(platform, version="")                                       # refused: empty
    with tenant_transaction(platform.ctx):
        assert AuditEvent.objects.filter(action="release.record").count() == 1


def test_another_platform_tenant_does_not_see_these_releases(platform):
    add(platform)
    other = Platform()
    other.staff("sam", "R11")
    assert releases(other) == []
    assert add(other).status_code == 201                            # the same version, its own log
    assert len(releases(platform)) == 1


def test_an_unknown_or_missing_login_is_refused(platform):
    assert platform.client.get(API + "platform/releases").status_code == 401
    assert listing(platform, "nobody").status_code in (401, 403)


def test_a_read_leaves_an_access_decision_on_record(platform):
    from modules.policy.models import AccessDecision
    releases(platform)
    with tenant_transaction(platform.ctx):
        assert AccessDecision.objects.filter(operation="read:platform", allowed=True).exists()


def test_the_service_refuses_a_date_with_a_time_of_day(platform):
    from foundation.cell_context import bind_context
    from foundation.errors import ErrorCode, ZoikoError
    from modules.policy.api import authorize
    actor = platform.staff("sam2", "R11")
    with bind_context(platform.ctx):
        decision = authorize(actor_public_id=actor, operation="write:platform",
                             purpose_code="SUPPORT", organization_public_id=platform.org)
    with tenant_transaction(platform.ctx), pytest.raises(ZoikoError) as caught:
        trust.add_release(version="1", summary="s", released_on=utc_now(), actor_public_id=actor,
                          decision=decision, organization_public_id=platform.org)
    assert caught.value.code == ErrorCode.VALIDATION_FAILED
    assert releases(platform) == []


def test_the_database_itself_refuses_to_change_or_remove_a_release_entry(platform):
    from django.db import DatabaseError, transaction

    from modules.trust.models import ReleaseNote
    add(platform)
    with tenant_transaction(platform.ctx):
        with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
            ReleaseNote.objects.update(summary="rewritten")
        with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
            ReleaseNote.objects.all().delete()
    assert releases(platform)[0]["summary"] == "Reports and work queue"
