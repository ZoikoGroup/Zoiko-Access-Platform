"""A circuit breaker per partner connector (DEV-221, GHE-20 C-08, GHE-13 T-32): a failing partner is
left alone for a while and its work waits locally; an unsupported answer halts only that connector;
while a clinic's booking system is down, staff may confirm by hand (the alternate path)."""
import datetime as dt
import json
import uuid

import pytest

from foundation.cell_context import bind_context, tenant_transaction
from foundation.timeutil import UTC, FixedClock
from modules.connector import adapters, circuit
from modules.connector import api as connector
from modules.connector.adapters import (
    AdapterUnavailable,
    AdapterUnsupported,
    Outcome,
    PartnerResult,
)
from modules.evidence.models import AuditEvent
from tests.test_bookingflow import Flow
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]
T0 = dt.datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


class Fake:
    """A partner whose behaviour the test sets: "ok", "down" or "unsupported"."""

    idempotent = True
    simulated = False

    def __init__(self, key):
        self.key, self.mode, self.calls = key, "ok", 0

    def _answer(self):
        self.calls += 1
        if self.mode == "down":
            raise AdapterUnavailable("down")
        if self.mode == "unsupported":
            raise AdapterUnsupported("profile v9")
        return PartnerResult(Outcome.CONFIRMED, partner_ref=f"P{self.calls}")

    def submit(self, operation):
        return self._answer()

    def query(self, operation):
        return self._answer()


@pytest.fixture
def fakes():
    adapters.clear()
    made = {k: Fake(k) for k in ("alpha", "beta")}
    for f in made.values():
        adapters.register(f)
    yield made
    adapters.clear()


@pytest.fixture
def world():
    return World()


def ask(world, key, clock):
    actor = world.person("R10")
    decision = world.ask(actor, "write:partner", "INTEGRATION", organization_public_id=world.org)
    with tenant_transaction(world.ctx):
        return connector.request_operation(
            connector_key=key, operation_type="booking.confirm", subject_ref="BK1",
            organization_ref=world.org, idempotency_key=uuid.uuid4().hex, request={},
            requested_by_ref=actor, decision=decision, clock=clock)


def tick(world, clock):
    with bind_context(world.ctx):
        connector.send_due(clock=clock)
        connector.reconcile_due(clock=clock)


def state_of(world, key, clock):
    with tenant_transaction(world.ctx):
        found = {c["connector_key"]: c for c in connector.circuits(clock)}
    return found.get(key, {"state": "closed"})


def fail_until_open(world, fake, clock):
    fake.mode = "down"
    for _ in range(circuit.THRESHOLD):
        ask(world, fake.key, clock)
    tick(world, clock)


def test_five_failures_in_a_row_open_the_circuit_and_work_waits_locally(world, fakes):
    clock = FixedClock(T0)
    fail_until_open(world, fakes["alpha"], clock)
    assert state_of(world, "alpha", clock)["state"] == "open"
    before = fakes["alpha"].calls
    waiting = ask(world, "alpha", clock)
    clock.advance(dt.timedelta(minutes=2))
    tick(world, clock)
    assert fakes["alpha"].calls == before                       # nothing sent while open
    with tenant_transaction(world.ctx):
        assert connector.get_operation(waiting["public_id"])["state"] == "pending"


def test_after_the_cool_down_one_trial_call_and_an_answer_closes_it(world, fakes):
    clock = FixedClock(T0)
    fail_until_open(world, fakes["alpha"], clock)
    fakes["alpha"].mode = "ok"
    clock.advance(circuit.FIRST_COOLDOWN + dt.timedelta(minutes=10))
    assert state_of(world, "alpha", clock)["state"] == "trial"
    before = fakes["alpha"].calls
    with bind_context(world.ctx):
        connector.send_due(clock=clock)
    assert fakes["alpha"].calls == before + 1                   # exactly one trial call
    assert state_of(world, "alpha", clock)["state"] == "closed"
    tick(world, clock)
    assert fakes["alpha"].calls > before + 1                    # the rest go now


def test_a_failed_trial_opens_it_again_for_twice_as_long(world, fakes):
    clock = FixedClock(T0)
    fail_until_open(world, fakes["alpha"], clock)
    clock.advance(circuit.FIRST_COOLDOWN + dt.timedelta(minutes=10))
    with bind_context(world.ctx):
        connector.send_due(clock=clock)
    found = state_of(world, "alpha", clock)
    assert found["state"] == "open"
    until = dt.datetime.fromisoformat(found["open_until"])
    assert until - clock.now() == 2 * circuit.FIRST_COOLDOWN


def test_an_unsupported_answer_halts_only_that_connector_until_a_person_resets_it(world, fakes):
    clock = FixedClock(T0)
    fakes["alpha"].mode = "unsupported"
    op = ask(world, "alpha", clock)
    other = ask(world, "beta", clock)
    tick(world, clock)
    assert state_of(world, "alpha", clock)["state"] == "halted"
    assert state_of(world, "beta", clock)["state"] == "closed"
    with tenant_transaction(world.ctx):
        assert connector.get_operation(other["public_id"])["state"] == "confirmed"
        assert connector.get_operation(op["public_id"])["state"] == "pending"
    calls = fakes["alpha"].calls
    clock.advance(dt.timedelta(hours=3))
    tick(world, clock)
    assert fakes["alpha"].calls == calls                        # waiting does not lift a halt
    fakes["alpha"].mode = "ok"
    with tenant_transaction(world.ctx):
        connector.reset_circuit("alpha", actor_ref="someone")
    clock.advance(dt.timedelta(hours=1))
    tick(world, clock)
    with tenant_transaction(world.ctx):
        assert connector.get_operation(op["public_id"])["state"] == "confirmed"
        actions = set(AuditEvent.objects.filter(action__startswith="connector.")
                      .values_list("action", flat=True))
    assert {"connector.halted", "connector.circuit_reset"} <= actions


def test_an_answer_resets_the_count_so_scattered_failures_do_not_open_it(world, fakes):
    clock = FixedClock(T0)
    for _ in range(circuit.THRESHOLD * 2):
        fakes["alpha"].mode = "down"
        ask(world, "alpha", clock)
        tick(world, clock)
        fakes["alpha"].mode = "ok"
        ask(world, "alpha", clock)
        tick(world, clock)
    assert state_of(world, "alpha", clock)["state"] == "closed"


# ------------------------------------------------------------------ the alternate path
@pytest.fixture(autouse=False)
def staff_login(settings):
    settings.DEBUG = True
    settings.DEV_AUTH_STUB = True


def test_while_the_booking_system_is_down_staff_may_confirm_by_hand(staff_login):
    from modules.connector.simulator import simulator

    adapters.clear()
    simulator.reset()
    adapters.register(simulator)
    try:
        flow = Flow()
        first, second = flow.request(0), flow.request(1)
        assert flow.confirm_by_hand(first).status_code == 409       # the system decides
        with bind_context(flow.ctx):
            circuit.halt("simulator", "unsupported")
        r = flow.confirm_by_hand(first)
        assert r.status_code == 200, r.content
        assert json.loads(r.content)["booking"]["status"] == "confirmed"
        with tenant_transaction(flow.ctx):
            connector.reset_circuit("simulator", actor_ref="someone")
        assert flow.confirm_by_hand(second).status_code == 409      # back to the system
    finally:
        adapters.clear()


def test_staff_see_the_circuits_and_only_permitted_staff_reset_them(staff_login):
    from modules.connector.simulator import simulator

    adapters.clear()
    simulator.reset()
    adapters.register(simulator)
    try:
        flow = Flow()
        with bind_context(flow.ctx):
            circuit.halt("simulator", "unsupported")
        shown = json.loads(flow.web.get("staff/connector/circuits", subject="desk").content)
        assert shown["circuits"][0]["state"] == "halted"
        assert flow.web.get("staff/connector/circuits", subject="pat").status_code == 403
        assert flow.web.post("staff/connector/circuits/simulator/reset",
                             subject="pat").status_code == 403
    finally:
        adapters.clear()
