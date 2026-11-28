"""A partner operation that gave up becomes work in the clinic's queue, so it stays tracked and
someone can claim it; a final answer closes it (GHE-13 T-55, DEV-226)."""
import datetime as dt
import uuid

import pytest

from foundation import dispatch
from foundation.cell_context import bind_context, tenant_transaction
from foundation.outbox import relay_once
from foundation.timeutil import UTC, FixedClock
from modules.connector import adapters
from modules.connector import api as connector
from modules.connector.models import ExternalOperation
from modules.connector.simulator import simulator
from modules.workflow import api as workflow
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def partner():
    adapters.clear()
    simulator.reset()
    adapters.register(simulator)
    yield
    adapters.clear()


@pytest.fixture(autouse=True)
def queue_listens():
    saved = dispatch.subscriptions()
    dispatch.clear_subscriptions()
    workflow.register_subscribers()
    yield
    dispatch.clear_subscriptions()
    for s in saved:
        dispatch.subscribe(s.consumer, s.prefix, s.handler)


def pump():
    for _ in range(6):
        relay_once(dispatch.dispatch)


def dead_operation(world):
    clock = FixedClock(dt.datetime(2026, 11, 10, 9, 0, tzinfo=UTC))
    actor = world.person("R10")
    decision = world.ask(actor, "write:partner", "INTEGRATION", organization_public_id=world.org)
    with tenant_transaction(world.ctx):
        op = connector.request_operation(
            connector_key="simulator", operation_type="booking.confirm", subject_ref="BK1",
            organization_ref=world.org, idempotency_key=uuid.uuid4().hex,
            request={"sim": "down", "sim_fail": 99}, requested_by_ref=actor, decision=decision,
            clock=clock)
        ExternalOperation.objects.filter(public_id=op["public_id"]).update(max_attempts=1)
    with bind_context(world.ctx):
        connector.send_due(clock=clock)
    pump()
    return op


def queue(world):
    with tenant_transaction(world.ctx):
        return [i for i in workflow.list_items(world.org) if i["kind"] == "settle_partner_op"]


def test_an_operation_that_gave_up_is_put_in_the_clinics_queue_once():
    world = World()
    op = dead_operation(world)
    items = queue(world)
    assert [i["subject_ref"] for i in items] == [op["public_id"]]
    pump()
    assert len(queue(world)) == 1                              # delivered twice, opened once


def test_settling_it_by_hand_closes_the_work():
    world = World()
    op = dead_operation(world)
    with tenant_transaction(world.ctx):
        connector.resolve_by_hand(operation_ref=op["public_id"], outcome="rejected",
                                  actor_ref="manager1")
    pump()
    assert queue(world) == []
