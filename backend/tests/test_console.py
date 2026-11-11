import datetime as dt
import json
import time
import uuid

import pytest
from django.test import Client

from foundation.cell_context import bind_context, tenant_transaction
from foundation.timeutil import UTC, FixedClock
from modules.connector import adapters
from modules.connector import api as connector
from modules.connector.models import ExternalOperation
from modules.connector.simulator import simulator
from modules.evidence.models import AuditEvent
from modules.packs import api as packs
from modules.packs.signing import build_signed_pack, generate_keypair
from modules.policy.api import decide
from modules.trust import api as trust
from modules.workflow import api as workflow
from tests.test_api import API, Web
from tests.test_platform import Platform

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

PUBLIC = "/api/public/v1/"
PUBLIC_URL = "https://8.8.8.8/hook"
LONG_AGO = FixedClock(dt.datetime(2020, 1, 1, tzinfo=UTC))


@pytest.fixture(autouse=True)
def dev_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


@pytest.fixture(autouse=True)
def partner():
    adapters.clear()
    simulator.reset()
    adapters.register(simulator)
    yield simulator
    adapters.clear()


class Console:
    """A clinic with a receptionist (R04), an administrator (R13), a partner engineer (R10) and a
    patient; plus a second clinic of the same customer with its own administrator."""

    def __init__(self):
        self.web = Web()
        self.ctx = self.web.clinic.ctx
        self.org = self.web.clinic.org
        self.web.patient("pat")
        self.desk = self.web.staff("desk")
        self.boss = self.web.login("boss", "facility_user", "R13")
        self.eng = self.web.login("eng", "facility_user", "R10")
        with tenant_transaction(self.ctx):
            self.other_org = trust.create_organization(name="Other Clinic").public_id
            rival = trust.register_actor(
                actor_type=trust.ActorType.FACILITY_USER, issuer="idp", subject="rival")
            trust.assign_role(actor_public_id=rival.public_id,
                              organization_public_id=self.other_org, role_code="R13", by="system")
            self.rival = rival.public_id

    def get(self, path, subject):
        return self.web.get(path, subject=subject)

    def post(self, path, body=None, subject="boss"):
        return self.web.post(path, body or {}, subject=subject)

    def json(self, response):
        return json.loads(response.content)

    # -- partner operations
    def operation(self, sim, *, org=None, who=None, max_attempts=5, **extra):
        org = org or self.org
        who = who or (self.eng, "INTEGRATION")
        with tenant_transaction(self.ctx):
            decision = decide(actor_public_id=who[0], operation="write:partner",
                              purpose_code=who[1], organization_public_id=org)
        with tenant_transaction(self.ctx):
            return connector.request_operation(
                connector_key="simulator", operation_type="booking.confirm", subject_ref="BK1",
                organization_ref=org, idempotency_key=uuid.uuid4().hex,
                request={"sim": sim, **extra}, requested_by_ref=who[0], decision=decision,
                max_attempts=max_attempts)

    def work(self, hours=0):
        """One worker pass (the clock moved on by `hours`)."""
        clock = FixedClock(dt.datetime.now(UTC) + dt.timedelta(hours=hours, minutes=1))
        with bind_context(self.ctx):
            connector.send_due(clock=clock)
            connector.reconcile_due(clock=clock)

    def state(self, op):
        with tenant_transaction(self.ctx):
            return connector.get_operation(op["public_id"])["state"]


@pytest.fixture
def c():
    return Console()


# ------------------------------------------------------------------ exceptions: partner operations
def test_the_screen_separates_what_needs_a_person_from_what_is_in_flight(c):
    lost = c.operation("timeout")                    # the reply is lost: outcome unknown
    gave_up = c.operation("down", sim_fail=50, max_attempts=1)
    waiting = c.operation("silent")                  # received, never answered
    done = c.operation("confirm_now")
    c.work()
    assert [c.state(o) for o in (lost, gave_up, waiting, done)] == [
        "unknown", "dead", "accepted", "confirmed"]
    body = c.json(c.get("staff/exceptions", "desk"))
    assert body["can_act"] is True
    assert {o["public_id"] for o in body["connector"]["attention"]} == {
        lost["public_id"], gave_up["public_id"]}
    assert [o["public_id"] for o in body["connector"]["waiting"]] == [waiting["public_id"]]
    assert [o["public_id"] for o in body["connector"]["recent"]] == [done["public_id"]]
    assert body["journeys"] == []
    shown = json.dumps(body)
    for private in ("idempotency_key", "request", "requested_by"):
        assert private not in shown                              # nothing but states and references


def test_another_clinic_sees_none_of_it_and_a_patient_may_not_open_the_screen(c):
    c.operation("timeout")
    c.work()
    rival = c.json(c.get("staff/exceptions", "rival"))
    assert rival["connector"] == {"attention": [], "waiting": [], "recent": []}
    assert c.get("staff/exceptions", "pat").status_code == 403
    assert c.web.client.get(API + "staff/exceptions").status_code == 401


def test_a_person_can_put_a_given_up_operation_back_in_the_queue(c):
    dead = c.operation("down", sim_fail=50, max_attempts=1)
    c.work()
    assert c.state(dead) == "dead"
    again = c.post(f"staff/connector/{dead['public_id']}/redrive", subject="desk")
    assert again.status_code == 200
    assert c.json(again)["operation"]["state"] == "pending"
    assert c.post(f"staff/connector/{dead['public_id']}/redrive", subject="desk"
                  ).status_code == 409                           # only a dead one


def test_settling_by_hand_is_recorded_and_never_counts_as_the_partners_word(c):
    lost = c.operation("timeout")
    c.work()
    bad = c.post(f"staff/connector/{lost['public_id']}/resolve", {"outcome": "maybe"})
    assert bad.status_code == 422
    done = c.post(f"staff/connector/{lost['public_id']}/resolve", {"outcome": "confirmed"},
                  subject="desk")
    operation = c.json(done)["operation"]
    assert (operation["state"], operation["source_confirmed"]) == ("confirmed", False)
    history = c.json(c.get(f"staff/connector/{lost['public_id']}/history", "desk"))["history"]
    assert history["chain_ok"] is True
    last = history["events"][-1]
    assert (last["event_type"], last["code"]) == ("resolved", "confirmed")
    assert last["by"] not in ("", "System") and "actor_ref" not in last   # a name, not an id


def test_the_history_names_the_system_for_what_nobody_did(c):
    op = c.operation("confirm_now")
    c.work()
    page = c.json(c.get(f"staff/connector/{op['public_id']}/history", "desk"))
    events = page["history"]["events"]
    assert [e["event_type"] for e in events] == ["requested", "sent", "confirmed"]
    assert {e["by"] for e in events if e["event_type"] in ("sent", "confirmed")} == {"System"}


def test_another_clinic_cannot_see_or_change_these_operations(c):
    lost = c.operation("timeout")
    c.work()
    for method, path, body in (
            ("get", f"staff/connector/{lost['public_id']}/history", None),
            ("post", f"staff/connector/{lost['public_id']}/redrive", {}),
            ("post", f"staff/connector/{lost['public_id']}/resolve", {"outcome": "confirmed"})):
        response = (c.get(path, "rival") if method == "get" else c.post(path, body, "rival"))
        assert response.status_code == 404, path
    assert c.state(lost) == "unknown"                              # untouched


def test_a_patient_cannot_act_on_operations(c):
    lost = c.operation("timeout")
    c.work()
    assert c.post(f"staff/connector/{lost['public_id']}/resolve", {"outcome": "rejected"},
                  subject="pat").status_code == 403


# ------------------------------------------------------------------ exceptions: journeys
def late_journey(c, org=None):
    workflow.load_bundled_definitions()
    with tenant_transaction(c.ctx):
        started = workflow.engine.start(
            definition_key="J1.booking", subject_ref=f"BK-{uuid.uuid4().hex[:6]}",
            owner_ref="doc1", actor_ref="reception1", organization_ref=org or c.org,
            clock=LONG_AGO)
    with tenant_transaction(c.ctx):
        assert workflow.engine.fire_due_timers() >= 1               # its deadline ran out long ago
    return started


def test_a_journey_deadline_that_ran_out_is_listed_and_can_be_resolved(c):
    journey = late_journey(c)
    body = c.json(c.get("staff/exceptions", "desk"))
    (item,) = body["journeys"]
    assert (item["detail"], item["instance_ref"], item["journey"]) == (
        "confirm_by", journey["public_id"], "J1.booking v1")
    assert item["journey_state"] == "requested"                    # the deadline moved nothing
    assert c.post(f"staff/workflow-exceptions/{item['public_id']}/resolve", subject="rival"
                  ).status_code == 404                              # another clinic's: not found
    done = c.post(f"staff/workflow-exceptions/{item['public_id']}/resolve", subject="desk")
    assert done.status_code == 200
    assert c.json(c.get("staff/exceptions", "desk"))["journeys"] == []


def test_journeys_show_the_released_definitions_and_only_this_clinics_journeys(c):
    journey = late_journey(c)
    other = late_journey(c, org=c.other_org)
    body = c.json(c.get("staff/journeys", "desk"))
    (definition,) = body["definitions"]
    assert (definition["key"], definition["version"], definition["state"]) == (
        "J1.booking", 1, "released")
    assert definition["initial"] == "requested" and "confirm" in definition["transitions"]
    assert definition["timers"] == {"confirm_by": 1440, "attendance_by": 10080}
    assert [i["public_id"] for i in body["instances"]] == [journey["public_id"]]
    assert body["by_state"] == {"J1.booking:requested": 1}
    theirs = c.json(c.get("staff/journeys", "rival"))
    assert [i["public_id"] for i in theirs["instances"]] == [other["public_id"]]


def test_a_journeys_steps_are_shown_by_name_and_hidden_from_other_clinics(c):
    journey = late_journey(c)
    history = c.json(c.get(f"staff/journeys/{journey['public_id']}/history", "desk"))["history"]
    assert history["chain_ok"] is True
    (step,) = history["steps"]
    assert (step["transition"], step["to_state"]) == ("start", "requested")
    assert "actor_ref" not in step and step["by"]
    assert c.get(f"staff/journeys/{journey['public_id']}/history", "rival").status_code == 404
    assert c.get("staff/journeys", "pat").status_code == 403


# ------------------------------------------------------------------ integrations
CLIENT = {"name": "Acme Labs", "environment": "production",
          "scopes": ["catalog:read", "webhooks:manage", "events:read"]}


def make_client(c, subject="boss", **over):
    return c.post("staff/developer/clients", {**CLIENT, **over}, subject=subject)


def test_an_administrator_makes_a_client_and_sees_its_key_once(c):
    made = make_client(c)
    body = c.json(made)
    assert made.status_code == 201 and made["Cache-Control"] == "no-store"
    assert body["key"].startswith("zk1.") and body["client"]["organization_ref"] == c.org
    listed = c.json(c.get("staff/developer", "boss"))
    (client,) = listed["clients"]
    assert client["public_id"] == body["client"]["public_id"] and client["webhooks"] == []
    assert listed["can_manage"] is True and "catalog:read" in listed["scopes"]
    assert body["key"] not in json.dumps(listed)                    # shown once, then never
    ping = Client().get(PUBLIC + "ping", HTTP_AUTHORIZATION=f"Bearer {body['key']}")
    assert ping.status_code == 200
    assert json.loads(ping.content)["client"] == client["public_id"]


def test_a_client_needs_valid_scopes_a_name_and_an_environment(c):
    for change in ({"scopes": []}, {"scopes": ["root"]}, {"scopes": "all"}, {"name": ""},
                   {"environment": "staging"}):
        assert make_client(c, **change).status_code == 422, change


def test_who_may_manage_clients_follows_the_role(c):
    assert c.get("staff/developer", "pat").status_code == 403
    assert c.get("staff/developer", "desk").status_code == 403     # no "partner" right for R04
    assert make_client(c, subject="desk").status_code == 403
    assert make_client(c, subject="eng").status_code == 201        # the partner engineer may
    assert c.get("staff/developer", "eng").status_code == 200


def test_another_clinic_sees_and_controls_none_of_these_clients(c):
    key = c.json(make_client(c))
    client = key["client"]["public_id"]
    assert c.json(c.get("staff/developer", "rival"))["clients"] == []
    for path in (f"staff/developer/clients/{client}/revoke",
                 f"staff/developer/clients/{client}/webhooks/x/test",
                 f"staff/developer/clients/{client}/deliveries/x/redrive"):
        assert c.post(path, subject="rival").status_code == 404, path
    ping = Client().get(PUBLIC + "ping", HTTP_AUTHORIZATION=f"Bearer {key['key']}")
    assert ping.status_code == 200                                  # still working


def test_revoking_a_client_stops_its_key_at_once(c):
    made = c.json(make_client(c))
    client = made["client"]["public_id"]
    revoked = c.post(f"staff/developer/clients/{client}/revoke")
    assert c.json(revoked)["client"]["status"] == "revoked"
    ping = Client().get(PUBLIC + "ping", HTTP_AUTHORIZATION=f"Bearer {made['key']}")
    assert ping.status_code == 401


def test_webhooks_and_their_deliveries_are_visible_and_a_test_ping_can_be_sent(c):
    made = c.json(make_client(c))
    client, key = made["client"]["public_id"], made["key"]
    hook = Client().post(
        PUBLIC + "webhooks", json.dumps({"url": PUBLIC_URL, "events": ["booking."]}),
        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {key}",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex)
    hook_id = json.loads(hook.content)["webhook"]["public_id"]
    ping = c.post(f"staff/developer/clients/{client}/webhooks/{hook_id}/test")
    assert ping.status_code == 202
    (listed,) = c.json(c.get("staff/developer", "boss"))["clients"]
    (shown,) = listed["webhooks"]
    assert shown["url"] == PUBLIC_URL and "secret" not in json.dumps(shown)
    (delivery,) = shown["deliveries"]
    assert (delivery["event_type"], delivery["state"]) == ("developer.ping.v1", "pending")
    # a delivery that is not given up cannot be redriven; and it must belong to this clinic's client
    assert c.post(f"staff/developer/clients/{client}/deliveries/{delivery['public_id']}/redrive"
                  ).status_code == 409
    assert c.post(f"staff/developer/clients/{client}/webhooks/nope/test").status_code == 404


# ------------------------------------------------------------------ platform: country packs
@pytest.fixture
def platform():
    p = Platform()
    p.support = p.staff("sam", "R11")
    p.safety = p.staff("tess", "R12")
    return p


def pack_page(p, subject):
    return p.client.get(API + "platform/packs", **p.header(p.tenant_public_id, subject))


def test_support_and_safety_staff_can_read_the_packs_page_and_it_is_audited(platform, settings):
    private, public = generate_keypair()
    settings.PACK_PUBLIC_KEYS = f"g1:{public}"
    now = dt.datetime.now(UTC)
    packs.install_pack(build_signed_pack(
        private_key_b64=private, key_id="g1", country="GB", version=1,
        issued_at=now - dt.timedelta(days=1), valid_from=now - dt.timedelta(days=1),
        expires_at=now + dt.timedelta(days=30), revocation_epoch=0,
        capabilities={"booking.request": {"enabled": True},
                      "referral.exchange": {"enabled": False}}))
    for who in ("sam", "tess"):
        response = pack_page(platform, who)
        assert response.status_code == 200
    body = json.loads(response.content)
    (row,) = body["packs"]
    assert (row["country"], row["version"], row["in_force"]) == ("GB", 1, True)
    assert row["capabilities"] == ["booking.request"]               # only what is switched on
    assert body["trusted_key_ids"] == ["g1"] and body["revocation_floor"] == 0
    assert public not in response.content.decode()                  # key ids only, never keys
    with tenant_transaction(platform.ctx):
        assert AuditEvent.objects.filter(action="platform.packs").count() == 2


def test_an_expired_pack_shows_as_not_in_force_even_though_it_is_still_installed(platform,
                                                                                  settings):
    private, public = generate_keypair()
    settings.PACK_PUBLIC_KEYS = f"g1:{public}"
    now = dt.datetime.now(UTC)
    packs.install_pack(build_signed_pack(
        private_key_b64=private, key_id="g1", country="FR", version=1,
        issued_at=now - dt.timedelta(days=9), valid_from=now - dt.timedelta(days=9),
        expires_at=now + dt.timedelta(seconds=2), revocation_epoch=0,
        capabilities={"booking.request": {"enabled": True}}))
    time.sleep(2.5)
    (row,) = json.loads(pack_page(platform, "sam").content)["packs"]
    assert (row["status"], row["in_force"]) == ("active", False)


def test_customers_and_patients_cannot_open_the_packs_page(platform, c):
    customer = c.web.client.get(API + "platform/packs", **c.web.header("boss"))
    assert customer.status_code == 403
    assert c.web.client.get(API + "platform/packs", **c.web.header("pat")).status_code == 403
    assert platform.client.get(API + "platform/packs").status_code == 401


def test_an_empty_cell_says_so_honestly(platform, settings):
    settings.PACK_PUBLIC_KEYS = ""
    body = json.loads(pack_page(platform, "sam").content)
    assert body == {"packs": [], "revocation_floor": 0, "trusted_key_ids": []}


def test_operations_of_another_clinic_stay_untouched_by_all_of_this(c):
    rival_op = c.operation("timeout", org=c.other_org, who=(c.rival, "OPERATIONS"))
    c.work()
    with tenant_transaction(c.ctx):
        assert ExternalOperation.objects.filter(public_id=rival_op["public_id"]).exists()
    assert c.json(c.get("staff/exceptions", "desk"))["connector"]["attention"] == []
    theirs = c.json(c.get("staff/exceptions", "rival"))["connector"]["attention"]
    assert [o["public_id"] for o in theirs] == [rival_op["public_id"]]
