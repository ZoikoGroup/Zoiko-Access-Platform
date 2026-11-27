"""Short-term support access (DEV-381): Zoiko support asks, the customer's administrator approves,
support looks for a few minutes, and every look (allowed or refused) is on the customer's record.
Mostly what must be REFUSED: looks before approval, after the end, outside the scopes granted, by
someone else, and anything at all without two-step sign-in."""
import datetime as dt
import json
from typing import Any

import pytest
from django.test import Client

from foundation.cell_context import tenant_transaction
from foundation.timeutil import utc_now
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.trust import support_access, two_step
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PASSWORD = "Correct-Horse-9"


@pytest.fixture(autouse=True)
def real_login_only(settings):
    settings.DEBUG = False
    settings.DEV_AUTH_STUB = False


class Clock:
    def __init__(self) -> None:
        self.at = utc_now()

    def __call__(self, clock: Any = None) -> dt.datetime:
        return self.at

    def wait(self, **delta: float) -> None:
        self.at += dt.timedelta(**delta)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(two_step, "utc_now", c)
    monkeypatch.setattr(support_access, "utc_now", c)
    return c


def call(method: str, path: str, token: str, **body: Any):
    auth: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    if method == "GET":
        return Client().get(f"/api/v1/{path}", **auth)
    return Client().post(f"/api/v1/{path}", json.dumps(body), content_type="application/json",
                         **auth)


def login(world: World, actor: str, name: str) -> None:
    with tenant_transaction(world.ctx):
        trust.set_password(actor, login=name, password=PASSWORD, by="system")


def sign_in(world: World, name: str) -> str:
    r = Client().post("/api/v1/auth/login", json.dumps(
        {"organization": world.tenant_name, "login": name, "password": PASSWORD}),
        content_type="application/json")
    assert r.status_code == 200 and r.json().get("token"), r.content
    return r.json()["token"]


def with_two_step(world: World, name: str, clock: Clock) -> tuple[str, str]:
    """(a session with two-step on, the app's secret)."""
    token = sign_in(world, name)
    secret = call("POST", "auth/two-step/set-up", token, password=PASSWORD).json()["secret"]
    done = call("POST", "auth/two-step/confirm", token, code=two_step.code_for(secret, clock.at))
    assert done.status_code == 200, done.content
    return done.json()["token"], secret


class Setting:
    def __init__(self, clock: Clock) -> None:
        self.zoiko = World(tenant_type=trust.TenantType.PLATFORM)
        self.support_id = self.zoiko.person("R11")
        login(self.zoiko, self.support_id, "sup")
        self.other_support_id = self.zoiko.person("R11")
        login(self.zoiko, self.other_support_id, "sup2")
        self.customer = World()
        self.admin_id = self.customer.person("R13")
        login(self.customer, self.admin_id, "boss")
        login(self.customer, self.customer.person("R04"), "ops")
        self.clock = clock
        self.support, self.secret = with_two_step(self.zoiko, "sup", clock)
        self.admin = sign_in(self.customer, "boss")

    def ask(self, token: str | None = None, **change: Any):
        body = {"customer": self.customer.tenant_public_id, "ticket": "ZD-1042",
                "reason": "Bookings are not showing for the North clinic",
                "scopes": ["setup", "activity"], "minutes": 20, **change}
        return call("POST", "platform/support-access", token or self.support, **body)

    def asked(self) -> str:
        r = self.ask()
        assert r.status_code == 201, r.content
        return r.json()["access"]["id"]

    def decide(self, access: str, step: str, token: str | None = None):
        return call("POST", f"staff/support-access/{access}/{step}", token or self.admin)

    def look(self, access: str, scope: str = "setup", token: str | None = None):
        return call("GET", f"platform/support-access/{self.customer.tenant_public_id}/{access}/"
                    f"{scope}", token or self.support)

    def customer_audit(self, action: str) -> set[tuple[str, str]]:
        with tenant_transaction(self.customer.ctx):
            return set(AuditEvent.objects.filter(action=action)
                       .values_list("outcome", "reason_code"))


@pytest.fixture
def s(clock):
    return Setting(clock)


# ------------------------------------------------------------------ asking
def test_support_without_two_step_cannot_even_ask(s):
    plain = sign_in(s.zoiko, "sup2")
    r = s.ask(plain)
    assert r.status_code == 403 and r.json()["fields"] == ["two_step"]
    assert call("GET", "platform/support-access", plain).status_code == 403


def test_only_zoiko_support_may_ask(s):
    r = call("POST", "platform/support-access", s.admin,
             customer=s.customer.tenant_public_id, ticket="ZD-1", reason="x" * 20,
             scopes=["setup"], minutes=10)
    assert r.status_code == 403                                 # a customer cannot


@pytest.mark.parametrize("change, field", [
    ({"ticket": ""}, "ticket"), ({"ticket": "has space"}, "ticket"),
    ({"reason": "short"}, "reason"), ({"scopes": []}, "scopes"),
    ({"scopes": ["patients"]}, "scopes"), ({"minutes": 60}, "minutes"),
    ({"minutes": True}, "minutes"),
])
def test_an_unusable_request_is_refused(s, change, field):
    r = s.ask(**change)
    assert r.status_code == 422 and field in r.json()["fields"]


def test_asking_an_unknown_customer_or_zoiko_itself_is_not_found(s):
    assert s.ask(customer="nope").status_code == 404
    assert s.ask(customer=s.zoiko.tenant_public_id).status_code == 404


def test_one_open_request_per_customer(s):
    s.asked()
    r = s.ask()
    assert r.status_code == 409 and r.json()["fields"] == ["open_request"]


def test_the_customer_sees_who_asked_and_why(s):
    access = s.asked()
    seen = call("GET", "staff/support-access", s.admin).json()
    row = next(r for r in seen["access"] if r["id"] == access)
    assert row["requester"] == "sup" and row["ticket"] == "ZD-1042"
    assert row["state"] == "requested" and row["scopes"] == ["activity", "setup"]
    assert seen["can_decide"] is True
    assert call("GET", "staff/support-access", sign_in(s.customer, "ops")).json()[
        "can_decide"] is False


def test_another_customer_never_sees_the_request(s):
    s.asked()
    other = World()
    login(other, other.person("R13"), "boss")
    assert call("GET", "staff/support-access", sign_in(other, "boss")).json()["access"] == []


# ------------------------------------------------------------------ the customer decides
def test_nothing_can_be_seen_before_approval_and_the_attempt_is_recorded(s):
    access = s.asked()
    r = s.look(access)
    assert r.status_code == 403 and r.json()["fields"] == ["requested"]
    assert ("denied", "requested") in s.customer_audit("support.access_used")


def test_only_the_administrator_approves(s):
    access = s.asked()
    assert s.decide(access, "approve", sign_in(s.customer, "ops")).status_code == 403
    assert s.decide(access, "approve").status_code == 200


def test_a_refused_request_opens_nothing(s):
    access = s.asked()
    assert s.decide(access, "refuse").json()["access"]["state"] == "refused"
    assert s.look(access).json()["fields"] == ["refused"]
    assert s.decide(access, "approve").status_code == 409      # decided once only


def test_an_unanswered_request_lapses_after_a_day(s, clock):
    access = s.asked()
    clock.wait(hours=25)
    assert s.decide(access, "approve").status_code == 409


# ------------------------------------------------------------------ looking
def test_an_approved_grant_opens_only_setup_and_status_never_people(s):
    access = s.asked()
    s.decide(access, "approve")
    r = s.look(access, "setup")
    assert r.status_code == 200, r.content
    view = r.json()["view"]
    assert [c["name"] for c in view["clinics"]] == ["North", "South"]
    text = json.dumps(r.json())
    for secret in ("boss", "ops", s.admin_id):                 # no logins, no ids of people
        assert secret not in text
    activity = s.look(access, "activity").json()["view"]
    assert activity["events"] and "bookings" in activity
    assert s.admin_id not in json.dumps(activity) and s.support_id not in json.dumps(activity)


def test_every_look_is_on_the_customers_record_and_counted(s):
    access = s.asked()
    s.decide(access, "approve")
    s.look(access)
    s.look(access, "activity")
    assert ("allowed", "") in s.customer_audit("support.access_used")
    row = call("GET", "staff/support-access", s.admin).json()["access"][0]
    assert row["uses"] == 2 and row["state"] == "active" and row["decided_by"]
    with tenant_transaction(s.zoiko.ctx):                      # and on Zoiko's own record
        assert AuditEvent.objects.filter(
            action="support.access_used", tenant_id=s.zoiko.ctx.tenant_id).count() == 2


def test_a_scope_not_granted_stays_closed(s):
    access = s.asked()
    s.decide(access, "approve")
    r = s.look(access, "deliveries")
    assert r.status_code == 403 and r.json()["fields"] == ["scope"]
    assert ("denied", "scope") in s.customer_audit("support.access_used")


def test_the_grant_ends_by_itself_and_later_looks_are_refused(s, clock):
    access = s.asked()
    s.decide(access, "approve")
    clock.wait(minutes=14)
    assert s.look(access).status_code == 200
    clock.wait(minutes=7)                                      # 21 minutes: past the 20 granted
    fresh = call("POST", "auth/two-step/recheck", s.support,   # (the code is due again too)
                 code=two_step.code_for(s.secret, clock.at)).json()["token"]
    r = s.look(access, token=fresh)
    assert r.status_code == 403 and r.json()["fields"] == ["expired"]
    assert ("denied", "expired") in s.customer_audit("support.access_used")


def test_the_customer_can_end_it_at_once(s):
    access = s.asked()
    s.decide(access, "approve")
    assert s.decide(access, "end").json()["access"]["state"] == "ended"
    assert s.look(access).json()["fields"] == ["ended"]


def test_support_can_end_only_their_own(s, clock):
    access = s.asked()
    s.decide(access, "approve")
    other, _ = with_two_step(s.zoiko, "sup2", clock)
    path = f"platform/support-access/{s.customer.tenant_public_id}/{access}/end"
    assert call("POST", path, other).status_code == 404
    assert s.look(access, token=other).status_code == 404       # nor look through it
    assert call("POST", path, s.support).json()["access"]["state"] == "ended"


def test_support_sees_their_requests_everywhere(s):
    access = s.asked()
    mine = call("GET", "platform/support-access", s.support).json()
    assert [r["id"] for r in mine["access"]] == [access]
    assert {c["code"] for c in mine["scopes"]} == {"setup", "activity", "deliveries"}
    assert mine["minutes"] == [10, 20, 30]


def test_looking_needs_a_recent_code(s, clock):
    access = s.asked()
    s.decide(access, "approve")
    clock.wait(minutes=16)
    r = s.look(access)
    assert r.status_code == 401 and r.json()["fields"] == ["two_step"]
