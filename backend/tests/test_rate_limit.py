import datetime as dt
import json
import uuid

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.timeutil import UTC, FixedClock
from modules.developer import api as developer
from modules.developer import services as dev_services
from modules.developer.models import RateWindow
from tests.conftest import as_app_role
from tests.test_api import Web
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PUBLIC = "/api/public/v1/"
NOON = dt.datetime(2026, 11, 10, 12, 0, 20, tzinfo=UTC)          # 20 seconds into a minute


def code_of(call, *args, **kwargs):
    with pytest.raises(ZoikoError) as caught:
        call(*args, **kwargs)
    return caught.value.code


class Partner:
    def __init__(self, limit=60, scopes=("catalog:read", "webhooks:manage"), world=None):
        self.world = world or World()
        self.ctx = self.world.ctx
        client, self.key = developer.create_client(
            tenant_public_id=self.world.tenant_public_id, name="Acme", environment="sandbox",
            scopes=list(scopes), created_by_ref="admin1", rate_limit_per_minute=limit)
        self.client = client

    def authed(self):
        return developer.authenticate_key(f"Bearer {self.key}")

    def get(self, path):
        return Client().get(PUBLIC + path, HTTP_AUTHORIZATION=f"Bearer {self.key}")


@pytest.fixture
def at_noon(monkeypatch):
    """Fix the clock the API reads, so a test never straddles a minute boundary."""
    now = {"t": NOON}
    monkeypatch.setattr(dev_services, "utc_now", lambda clock=None: now["t"])
    return now


# ------------------------------------------------------------------ the limit itself
def test_a_new_client_gets_the_standard_limit_and_it_can_be_chosen_within_bounds():
    standard = Partner()
    assert standard.client["rate_limit_per_minute"] == developer.DEFAULT_RATE_LIMIT == 60
    assert Partner(limit=1).client["rate_limit_per_minute"] == 1
    assert Partner(limit=developer.MAX_RATE_LIMIT).authed().rate_limit == 600
    world = World()
    for bad in (0, -1, 601, True, "5", 1.5, None):
        assert code_of(developer.create_client, tenant_public_id=world.tenant_public_id,
                       name="A", environment="sandbox", scopes=["catalog:read"],
                       created_by_ref="admin1", rate_limit_per_minute=bad) == (
            ErrorCode.VALIDATION_FAILED), bad


def test_requests_are_counted_against_the_minute_and_refused_over_the_limit():
    p = Partner(limit=3)
    client = p.authed()
    clock = FixedClock(NOON)
    seen = [developer.check_rate(client, clock=clock) for _ in range(5)]
    assert [s.allowed for s in seen] == [True, True, True, False, False]
    assert [s.remaining for s in seen] == [2, 1, 0, 0, 0]       # hammering never resets it
    assert {s.limit for s in seen} == {3} and {s.reset_seconds for s in seen} == {40}


def test_a_new_minute_starts_a_new_count_and_old_minutes_are_removed():
    p = Partner(limit=1)
    client = p.authed()
    clock = FixedClock(NOON)
    assert developer.check_rate(client, clock=clock).allowed
    assert not developer.check_rate(client, clock=clock).allowed
    clock.advance(dt.timedelta(minutes=1))
    assert developer.check_rate(client, clock=clock).allowed            # a fresh minute
    clock.advance(dt.timedelta(hours=2))
    assert developer.check_rate(client, clock=clock).allowed
    with tenant_transaction(p.ctx):
        assert RateWindow.objects.count() == 1                           # only the newest remains


def test_the_cleanup_works_with_the_rights_the_application_really_has():
    p = Partner(limit=5)
    client = p.authed()
    old = NOON - dt.timedelta(hours=3)
    with tenant_transaction(p.ctx):
        RateWindow.objects.create(client_ref=client.client_ref, window_start=old, count=9)
    with tenant_transaction(p.ctx), as_app_role():                       # not a superuser
        status = developer.check_rate(client, clock=FixedClock(NOON))
        assert status.allowed
        assert [w.count for w in RateWindow.objects.all()] == [1]


def test_each_client_has_its_own_count_and_so_does_each_customer():
    one, two = Partner(limit=1), Partner(limit=1)
    other_client_same_customer = Partner(limit=1, world=one.world)
    clock = FixedClock(NOON)
    assert developer.check_rate(one.authed(), clock=clock).allowed
    assert not developer.check_rate(one.authed(), clock=clock).allowed
    assert developer.check_rate(two.authed(), clock=clock).allowed
    assert developer.check_rate(other_client_same_customer.authed(), clock=clock).allowed
    with tenant_transaction(two.ctx), as_app_role():                     # and the database agrees
        assert RateWindow.objects.count() == 1


# ------------------------------------------------------------------ over HTTP
def test_the_api_answers_429_with_retry_after_and_tells_the_client_where_it_stands(at_noon):
    p = Partner(limit=2)
    first, second, third = p.get("ping"), p.get("ping"), p.get("ping")
    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 429)
    assert first["X-RateLimit-Limit"] == "2" and first["X-RateLimit-Remaining"] == "1"
    assert second["X-RateLimit-Remaining"] == "0" and second["X-RateLimit-Reset"] == "40"
    body = json.loads(third.content)
    assert body["code"] == "RATE_LIMITED" and body["retryable"] is True
    assert third["Retry-After"] == "40" and third["X-RateLimit-Remaining"] == "0"
    assert third["Content-Type"] == "application/problem+json"
    other = Partner(limit=2)
    assert other.get("ping").status_code == 200                           # not affected
    at_noon["t"] += dt.timedelta(seconds=41)                              # the next minute
    assert p.get("ping").status_code == 200


def test_a_refused_request_does_nothing(at_noon):
    p = Partner(limit=1, scopes=("webhooks:manage",))
    assert p.get("webhooks").status_code == 200
    over = Client().post(PUBLIC + "webhooks", json.dumps({"url": "https://8.8.8.8/x",
                                                            "events": ["booking."]}),
                         content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {p.key}",
                         HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex)
    assert over.status_code == 429
    with tenant_transaction(p.ctx):
        assert developer.list_webhooks(p.client["public_id"]) == []        # nothing was created


def test_a_request_the_client_may_not_make_still_counts(at_noon):
    p = Partner(limit=2, scopes=("catalog:read",))
    assert p.get("webhooks").status_code == 403                           # no such scope
    assert p.get("webhooks").status_code == 403
    assert p.get("webhooks").status_code == 429                           # but it used the quota


def test_requests_that_are_not_from_a_client_are_not_counted_against_anyone(at_noon):
    p = Partner(limit=1)
    for _ in range(5):
        assert Client().get(PUBLIC + "openapi.json").status_code == 200   # public: no key
        forged = Client().get(PUBLIC + "ping", HTTP_AUTHORIZATION="Bearer zk1.a.b.c")
        assert forged.status_code == 401
    with tenant_transaction(p.ctx):
        assert RateWindow.objects.count() == 0
    assert p.get("ping").status_code == 200                               # the quota is untouched


def test_the_contract_describes_the_limit():
    spec = json.loads(Client().get(PUBLIC + "openapi.json").content)
    assert "X-RateLimit-Remaining" in spec["x-rate-limit"]["headers"]
    assert "429" in spec["x-rate-limit"]["over_limit"]


# ------------------------------------------------------------------ the staff screen's side
@pytest.fixture
def staff(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True
    web = Web()
    web.login("boss", "facility_user", "R13")
    return web


def test_an_administrator_sets_the_limit_when_making_a_client_and_sees_it_listed(staff):
    body = {"name": "Acme", "environment": "sandbox", "scopes": ["catalog:read"]}
    made = staff.post("staff/developer/clients", {**body, "rate_limit": 5}, subject="boss")
    assert made.status_code == 201
    assert json.loads(made.content)["client"]["rate_limit_per_minute"] == 5
    plain = staff.post("staff/developer/clients", body, subject="boss")
    assert json.loads(plain.content)["client"]["rate_limit_per_minute"] == 60
    for bad in (0, 601, "5", True):
        refused = staff.post("staff/developer/clients", {**body, "rate_limit": bad},
                             subject="boss")
        assert refused.status_code == 422, bad
    listed = json.loads(staff.get("staff/developer", subject="boss").content)
    assert sorted(c["rate_limit_per_minute"] for c in listed["clients"]) == [5, 60]
    assert (listed["default_rate_limit"], listed["max_rate_limit"]) == (60, 600)
