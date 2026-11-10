import datetime as dt
import json
from typing import Any

import pytest
from django.test import Client

from foundation.cell_context import bind_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import utc_now
from modules.evidence.models import AuditEvent
from modules.policy.api import authorize
from modules.policy.models import AccessDecision
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


def post(world, path, body, subject="sam"):
    return world.client.post(API + path, json.dumps(body), content_type="application/json",
                             **world.header(world.tenant_public_id, subject))


def add(world, subject="sam", **fields):
    body = {"title": "Mail delayed", "severity": "sev2",
            "happened_on": today().isoformat(), **fields}
    return post(world, "platform/incidents", body, subject)


def resolve(world, incident, subject="sam", note="Mail provider recovered", **extra):
    return post(world, f"platform/incidents/{incident}/resolve", {"resolution": note, **extra},
                subject)


def listing(world, subject="sam"):
    return world.client.get(API + "platform/incidents",
                            **world.header(world.tenant_public_id, subject))


def incidents(world, subject="sam"):
    r = listing(world, subject)
    assert r.status_code == 200, r.content
    return json.loads(r.content)["incidents"]


def made(world, **fields):
    r = add(world, **fields)
    assert r.status_code == 201, r.content
    return json.loads(r.content)["incident"]["id"]


# ------------------------------------------------------------------ recording
def test_support_records_an_incident_and_it_starts_open(platform):
    r = add(platform, details="Two hours of late mail.\nNo data lost.", severity="sev1")
    assert r.status_code == 201
    [row] = incidents(platform)
    assert (row["title"], row["severity"], row["status"]) == ("Mail delayed", "sev1", "open")
    assert row["details"] == "Two hours of late mail.\nNo data lost."
    assert row["happened_on"] == today().isoformat()
    assert row["recorded_by"] and row["resolved_on"] is None and row["resolution"] == ""


def test_an_empty_log_is_an_empty_list(platform):
    assert incidents(platform) == []


@pytest.mark.parametrize("severity", ["sev0", "sev1", "sev2", "sev3"])
def test_the_four_severities_are_accepted(platform, severity):
    assert add(platform, severity=severity).status_code == 201


@pytest.mark.parametrize("fields", [
    {"title": ""}, {"title": "   "}, {"title": "t" * 121}, {"title": "bell\x07"},
    {"title": "two\nlines"}, {"title": 5}, {"title": None},
    {"severity": "critical"}, {"severity": ""}, {"severity": None}, {"severity": 2},
    {"severity": ["sev3"]}, {"severity": "SEV1"}, {"severity": "high"}, {"severity": "low"},
    {"severity": "medium"}, {"severity": "sev4"},
    {"details": "d" * 2001}, {"details": "bell\x07"}, {"details": 5},
    {"happened_on": "not-a-date"}, {"happened_on": ""}, {"happened_on": "2026-13-40"},
    {"happened_on": (today() + dt.timedelta(days=2)).isoformat()},
])
def test_bad_entries_are_refused_and_nothing_is_saved(platform, fields):
    assert add(platform, **fields).status_code == 422
    assert incidents(platform) == []


def test_the_longest_allowed_entry_is_accepted(platform):
    assert add(platform, title="t" * 120, details="d" * 2000).status_code == 201


def test_tomorrow_is_allowed_for_time_zones_but_not_the_day_after(platform):
    assert add(platform, happened_on=(today() + dt.timedelta(days=1)).isoformat()
               ).status_code == 201
    assert add(platform, happened_on=(today() + dt.timedelta(days=2)).isoformat()
               ).status_code == 422


def test_two_incidents_may_share_a_title(platform):
    made(platform)
    made(platform)
    assert len(incidents(platform)) == 2


def test_open_ones_come_first_then_the_newest_date_then_the_newest_entry(platform):
    old = made(platform, title="old open", happened_on=(today() - dt.timedelta(days=9)).isoformat())
    closed = made(platform, title="closed", happened_on=today().isoformat())
    made(platform, title="new open", happened_on=today().isoformat())
    made(platform, title="same day later", happened_on=today().isoformat())
    resolve(platform, closed)
    assert [r["title"] for r in incidents(platform)] == [
        "same day later", "new open", "old open", "closed"]
    assert old


# ------------------------------------------------------------------ closing
def test_support_closes_an_incident_with_a_note_and_todays_date(platform):
    incident = made(platform)
    r = resolve(platform, incident, resolved_on="2020-01-01")       # a typed date is ignored
    assert r.status_code == 200
    row = json.loads(r.content)["incident"]
    assert row["status"] == "resolved" and row["resolution"] == "Mail provider recovered"
    assert row["resolved_on"] == today().isoformat() and row["resolved_by"]
    assert incidents(platform)[0]["status"] == "resolved"


def test_a_closed_incident_cannot_be_closed_again_and_keeps_its_first_note(platform):
    incident = made(platform)
    resolve(platform, incident, note="first note")
    assert resolve(platform, incident, note="second note").status_code == 409
    assert incidents(platform)[0]["resolution"] == "first note"


@pytest.mark.parametrize("note", ["", "   ", "n" * 1001, "bell\x07", 5, None, ["x"]])
def test_a_bad_closing_note_is_refused_and_the_incident_stays_open(platform, note):
    incident = made(platform)
    assert resolve(platform, incident, note=note).status_code == 422
    assert incidents(platform)[0]["status"] == "open"


def test_the_longest_closing_note_and_line_breaks_are_accepted(platform):
    incident = made(platform)
    assert resolve(platform, incident, note=("n" * 998) + "\nx").status_code == 200


def test_an_unknown_incident_is_not_found(platform):
    assert resolve(platform, "incident_missing").status_code == 404


def test_the_title_and_severity_do_not_change_when_closing(platform):
    incident = made(platform, title="Keep me", severity="sev1")
    resolve(platform, incident, title="Changed", severity="sev3")
    row = incidents(platform)[0]
    assert (row["title"], row["severity"]) == ("Keep me", "sev1")


# ------------------------------------------------------------------ who may do what
def test_the_safety_officer_can_read_but_neither_add_nor_close(platform):
    platform.staff("iyer", "R12")
    incident = made(platform)
    assert len(incidents(platform, "iyer")) == 1
    assert add(platform, "iyer").status_code == 403
    assert resolve(platform, incident, "iyer").status_code == 403
    assert len(incidents(platform)) == 1 and incidents(platform)[0]["status"] == "open"


@pytest.mark.parametrize("role,kind", [("R10", trust.ActorType.WORKLOAD),
                                       ("R04", trust.ActorType.FACILITY_USER)])
def test_other_platform_roles_can_do_nothing_here(platform, role, kind):
    incident = made(platform)
    platform.staff("other", role, kind=kind)
    assert listing(platform, "other").status_code == 403
    assert add(platform, "other").status_code == 403
    assert resolve(platform, incident, "other").status_code == 403
    assert incidents(platform)[0]["status"] == "open"


def test_a_customers_staff_cannot_use_it_even_with_a_support_role():
    customer = World()                                              # a clinic, not the platform
    with tenant_transaction(customer.ctx):
        actor = trust.register_actor(actor_type=trust.ActorType.SUPPORT, issuer="idp", subject="x")
        trust.assign_role(actor_public_id=actor.public_id, organization_public_id=customer.org,
                          role_code="R11", by="system")
    client = Client()
    header: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer dev:{customer.tenant_public_id}:idp:x"}
    body = json.dumps({"title": "t", "severity": "sev3", "happened_on": today().isoformat()})
    assert client.get(API + "platform/incidents", **header).status_code == 403
    assert client.post(API + "platform/incidents", body, content_type="application/json",
                       **header).status_code == 403
    assert client.post(API + "platform/incidents/x/resolve", json.dumps({"resolution": "r"}),
                       content_type="application/json", **header).status_code == 403


def test_another_platform_tenant_neither_sees_nor_closes_these(platform):
    incident = made(platform)
    other = Platform()
    other.staff("sam", "R11")
    assert incidents(other) == []
    assert resolve(other, incident).status_code == 404
    assert incidents(platform)[0]["status"] == "open"


def test_an_unknown_or_missing_login_is_refused(platform):
    assert platform.client.get(API + "platform/incidents").status_code == 401
    assert listing(platform, "nobody").status_code in (401, 403)


# ------------------------------------------------------------------ the record
def test_each_change_is_audited_once_and_a_refusal_is_not(platform):
    incident = made(platform)
    add(platform, title="")                                         # refused
    resolve(platform, incident)
    resolve(platform, incident)                                     # refused: already closed
    resolve(platform, incident, note="")                            # refused
    with tenant_transaction(platform.ctx):
        assert AuditEvent.objects.filter(action="incident.record").count() == 1
        assert AuditEvent.objects.filter(action="incident.resolve").count() == 1


def test_a_read_leaves_an_access_decision_on_record(platform):
    incidents(platform)
    with tenant_transaction(platform.ctx):
        assert AccessDecision.objects.filter(operation="read:platform", allowed=True).exists()


def test_closing_leaves_a_write_decision_on_record(platform):
    resolve(platform, made(platform))
    with tenant_transaction(platform.ctx):
        assert AccessDecision.objects.filter(operation="write:platform", allowed=True).count() >= 2


def test_the_service_refuses_a_date_with_a_time_of_day_and_an_unknown_incident(platform):
    actor = platform.staff("sam2", "R11")
    with bind_context(platform.ctx):
        decision = authorize(actor_public_id=actor, operation="write:platform",
                             purpose_code="SUPPORT", organization_public_id=platform.org)
    with tenant_transaction(platform.ctx), pytest.raises(ZoikoError) as caught:
        trust.add_incident(title="t", severity="sev3", happened_on=utc_now(), actor_public_id=actor,
                           decision=decision, organization_public_id=platform.org)
    assert caught.value.code == ErrorCode.VALIDATION_FAILED
    with tenant_transaction(platform.ctx), pytest.raises(ZoikoError) as missing:
        trust.resolve_incident(incident_ref="incident_missing", resolution="r",
                               actor_public_id=actor, decision=decision,
                               organization_public_id=platform.org)
    assert missing.value.code == ErrorCode.NOT_FOUND
    assert incidents(platform) == []
