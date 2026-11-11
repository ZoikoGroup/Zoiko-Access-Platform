import datetime as dt
import uuid

import pytest
from django.db import DatabaseError, connection, transaction

from foundation import periodic
from foundation.cell_context import bind_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.models import OutboxEvent
from foundation.timeutil import UTC, FixedClock
from modules.connector import adapters, jobs
from modules.connector import api as connector
from modules.connector.adapters import Outcome, PartnerResult
from modules.connector.models import (
    ExternalOperation,
    OperationEvent,
    OperationState,
)
from modules.connector.simulator import simulator
from modules.evidence.models import AuditEvent
from modules.workflow import api as workflow
from tests.conftest import as_app_role
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

T0 = dt.datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
HOUR = dt.timedelta(hours=1)


@pytest.fixture(autouse=True)
def partner():
    adapters.clear()
    simulator.reset()
    adapters.register(simulator)
    yield simulator
    adapters.clear()


@pytest.fixture
def world():
    return World()


@pytest.fixture
def clock():
    return FixedClock(T0)


def code_of(call, *args, **kwargs):
    with pytest.raises(ZoikoError) as caught:
        call(*args, **kwargs)
    return caught.value.code


def receipt(world, role="R10", purpose="INTEGRATION"):
    actor = world.person(role)
    return actor, world.ask(actor, "write:partner", purpose, organization_public_id=world.org)


def ask_partner(world, clock, *, sim="confirm_now", key=None, who=None, **extra):
    actor, decision = who or receipt(world)
    with tenant_transaction(world.ctx):
        return connector.request_operation(
            connector_key="simulator", operation_type="booking.confirm", subject_ref="BK1",
            organization_ref=world.org, idempotency_key=key or uuid.uuid4().hex,
            request={"sim": sim, **extra}, requested_by_ref=actor, decision=decision,
            clock=clock)


def tick(world, clock):
    """One pass of the worker for this customer: send what is due, then ask about the rest."""
    with bind_context(world.ctx):
        return connector.send_due(clock=clock), connector.reconcile_due(clock=clock)


def later(world, clock, delta=HOUR):
    clock.advance(delta)
    return tick(world, clock)


def get(world, op):
    with tenant_transaction(world.ctx):
        return connector.get_operation(op["public_id"])


def kinds(world, op):
    with tenant_transaction(world.ctx):
        return [e["event_type"] for e in connector.operation_history(op["public_id"])["events"]]


def submits():
    return [c for c in simulator.calls if c[0] == "submit"]


# ------------------------------------------------------------------ asking
def test_a_request_is_queued_and_nothing_is_sent_yet(world, clock):
    op = ask_partner(world, clock)
    assert (op["state"], op["attempts"], op["source_confirmed"]) == ("pending", 0, False)
    assert simulator.calls == []                               # asking is not sending


def test_asking_needs_a_policy_receipt_for_exactly_that_person_and_organization(world, clock):
    asker, decision = receipt(world)
    other = world.person("R10")
    assert code_of(ask_partner, world, clock, who=(other, decision)) == ErrorCode.POLICY_DENIED
    ops = world.person("R04")                          # clinic operations may not drive partners
    denied = world.ask(ops, "write:partner", "OPERATIONS", organization_public_id=world.org)
    assert denied.allowed is False
    assert code_of(ask_partner, world, clock, who=(ops, denied)) == ErrorCode.POLICY_DENIED
    assert ExternalOperation.objects.count() == 0


def test_a_request_is_checked_before_anything_is_stored(world, clock):
    who = receipt(world)
    actor, decision = who

    def call(**over):
        args = {"connector_key": "simulator", "operation_type": "booking.confirm",
                "subject_ref": "BK1", "organization_ref": world.org,
                "idempotency_key": uuid.uuid4().hex, "request": {"sim": "confirm"},
                "requested_by_ref": actor, "decision": decision, "clock": clock, **over}
        with tenant_transaction(world.ctx):
            return connector.request_operation(**args)

    assert code_of(call, connector_key="nobody") == ErrorCode.VALIDATION_FAILED
    assert code_of(call, idempotency_key="short") == ErrorCode.VALIDATION_FAILED
    assert code_of(call, request={"sim": {"nested": 1}}) == ErrorCode.VALIDATION_FAILED
    assert code_of(call, request={"note": "x" * 500}) == ErrorCode.VALIDATION_FAILED
    assert code_of(call, max_attempts=0) == ErrorCode.VALIDATION_FAILED
    assert code_of(call, max_attempts=11) == ErrorCode.VALIDATION_FAILED
    assert ExternalOperation.objects.count() == 0


def test_asking_twice_with_the_same_key_is_one_operation(world, clock):
    who = receipt(world)
    key = uuid.uuid4().hex
    first = ask_partner(world, clock, key=key, who=who)
    again = ask_partner(world, clock, key=key, who=who)
    assert again["public_id"] == first["public_id"]
    assert ExternalOperation.objects.count() == 1
    different = code_of(ask_partner, world, clock, key=key, who=who, sim="reject")
    assert different == ErrorCode.DUPLICATE_COMMAND            # same key, different request


# ------------------------------------------------------------------ the partner's answers
def test_a_receipt_is_not_a_confirmation_until_the_partner_says_so(world, clock):
    op = ask_partner(world, clock, sim="confirm")
    tick(world, clock)
    seen = get(world, op)
    assert (seen["state"], seen["source_confirmed"], seen["attempts"]) == ("accepted", False, 1)
    assert tick(world, clock) == (0, 0)                        # not due again yet: no pestering
    later(world, clock, dt.timedelta(minutes=2))
    done = get(world, op)
    assert (done["state"], done["source_confirmed"]) == ("confirmed", True)
    assert done["finished_at"] is not None
    assert kinds(world, op) == ["requested", "sent", "accepted", "confirmed"]
    assert len(submits()) == 1                                 # asked once, then only questions


def test_a_partner_that_confirms_straight_away_is_believed_once(world, clock):
    op = ask_partner(world, clock, sim="confirm_now")
    tick(world, clock)
    assert get(world, op)["state"] == "confirmed"
    assert simulator.calls == [("submit", simulator.calls[0][1])]


def test_a_refusal_is_recorded_with_the_partners_reason(world, clock):
    op = ask_partner(world, clock, sim="reject")
    tick(world, clock)
    seen = get(world, op)
    assert (seen["state"], seen["last_code"], seen["source_confirmed"]) == (
        "rejected", "slot_taken", False)
    assert seen["finished_at"] is not None


def test_only_the_confirmed_state_counts_as_source_confirmed():
    for state in OperationState:
        assert connector.is_source_confirmed(ExternalOperation(state=state)) is (
            state == OperationState.CONFIRMED)


# ------------------------------------------------------------------ not knowing
def test_a_call_that_may_have_arrived_is_never_simply_sent_again(world, clock):
    op = ask_partner(world, clock, sim="timeout")          # the partner did it; reply is lost
    tick(world, clock)
    seen = get(world, op)
    assert (seen["state"], seen["last_error"]) == ("unknown", "AdapterTimeout")
    later(world, clock, dt.timedelta(minutes=2))               # reconciliation asks the partner
    assert get(world, op)["state"] == "confirmed"              # it had worked after all
    assert len(submits()) == 1                                 # and we never resent it


def test_a_call_the_partner_never_got_is_sent_again_with_the_same_key(world, clock):
    key = uuid.uuid4().hex
    op = ask_partner(world, clock, sim="timeout_lost", key=key)
    tick(world, clock)
    assert get(world, op)["state"] == "unknown"
    later(world, clock, dt.timedelta(minutes=2))               # the partner has no record of it
    assert get(world, op)["state"] == "pending"
    later(world, clock, dt.timedelta(minutes=2))               # now it is safe to send again
    assert get(world, op)["state"] == "confirmed"
    assert [k for _, k in submits()] == [key, key]             # twice, with the SAME key


class Broken:
    key = "broken"
    idempotent = False

    def __init__(self):
        self.submitted = 0

    def submit(self, operation):
        self.submitted += 1
        raise RuntimeError("an adapter bug")

    def query(self, operation):
        return PartnerResult(Outcome.NOT_FOUND)


def test_an_adapter_that_cannot_promise_idempotence_is_never_resent_blindly(world, clock):
    broken = Broken()
    adapters.register(broken)
    actor, decision = receipt(world)
    with tenant_transaction(world.ctx):
        op = connector.request_operation(
            connector_key="broken", operation_type="x.do", subject_ref="S1",
            organization_ref=world.org, idempotency_key=uuid.uuid4().hex, request={},
            requested_by_ref=actor, decision=decision, clock=clock)
    tick(world, clock)
    assert get(world, op)["state"] == "unknown"                # a bug is "unknown", not "failed"
    for _ in range(3):
        later(world, clock)                                # partner: "never heard of it"
    assert broken.submitted == 1                               # but we still do not guess
    assert get(world, op)["state"] in ("unknown", "dead")


def test_a_worker_that_died_mid_call_leaves_an_operation_that_becomes_unknown(world, clock):
    stuck = ask_partner(world, clock)
    recent = ask_partner(world, clock)
    with tenant_transaction(world.ctx):
        ExternalOperation.objects.filter(public_id=stuck["public_id"]).update(
            state="sent", sent_at=T0 - dt.timedelta(minutes=10))
        ExternalOperation.objects.filter(public_id=recent["public_id"]).update(
            state="sent", sent_at=T0)
    with bind_context(world.ctx):
        assert connector.recover_stuck(clock=clock) == 1
    assert (get(world, stuck)["state"], get(world, stuck)["last_error"]) == (
        "unknown", "WorkerLost")
    assert get(world, recent)["state"] == "sent"               # still within its time


# ------------------------------------------------------------------ failing
def test_a_partner_that_is_down_is_retried_with_a_growing_wait(world, clock):
    op = ask_partner(world, clock, sim="down", sim_fail=2)
    tick(world, clock)
    seen = get(world, op)
    assert (seen["state"], seen["attempts"], seen["last_error"]) == (
        "pending", 1, "AdapterUnavailable")
    assert tick(world, clock) == (0, 0)                        # waiting: nothing is retried early
    later(world, clock)
    assert (get(world, op)["state"], get(world, op)["attempts"]) == ("pending", 2)
    later(world, clock)
    done = get(world, op)
    assert (done["state"], done["attempts"]) == ("confirmed", 3)
    assert len(submits()) == 3
    assert kinds(world, op).count("retry") == 2


def test_an_operation_that_keeps_failing_is_given_up_and_a_person_can_redrive_it(world, clock):
    asker, decision = receipt(world)
    op = ask_partner(world, clock, sim="down", sim_fail=50, who=(asker, decision))
    with tenant_transaction(world.ctx):                         # allow only two attempts
        ExternalOperation.objects.filter(public_id=op["public_id"]).update(max_attempts=2)
    tick(world, clock)
    later(world, clock)
    dead = get(world, op)
    assert (dead["state"], dead["attempts"]) == ("dead", 2)
    with tenant_transaction(world.ctx):
        again = connector.redrive(operation_ref=op["public_id"], actor_ref="manager1",
                                  clock=clock)
    assert (again["state"], again["attempts"]) == ("pending", 0)
    assert kinds(world, op)[-1] == "redriven"
    with tenant_transaction(world.ctx):
        assert code_of(connector.redrive, operation_ref=op["public_id"],
                       actor_ref="m") == ErrorCode.UNSAFE_REPLAY       # only dead ones


def test_a_partner_that_never_gives_a_final_answer_ends_up_needing_a_person(world, clock):
    op = ask_partner(world, clock, sim="silent")
    with tenant_transaction(world.ctx):
        ExternalOperation.objects.filter(public_id=op["public_id"]).update(max_polls=2)
    tick(world, clock)
    assert get(world, op)["state"] == "accepted"
    later(world, clock)
    assert get(world, op)["state"] == "accepted"
    later(world, clock)
    assert get(world, op)["state"] == "dead"
    with tenant_transaction(world.ctx):
        settled = connector.resolve_by_hand(
            operation_ref=op["public_id"], outcome="confirmed", actor_ref="manager1", clock=clock)
    # A person settled it: the state says so, but it is never passed off as the partner's word.
    assert (settled["state"], settled["source_confirmed"]) == ("confirmed", False)
    assert settled["last_code"] == "manual_resolution"
    with tenant_transaction(world.ctx):
        history = connector.operation_history(op["public_id"])
    last = history["events"][-1]
    assert (last["event_type"], last["actor_ref"], last["code"]) == (
        "resolved", "manager1", "confirmed")                   # the record says WHO settled it


def test_settling_by_hand_is_limited_to_operations_that_are_stuck(world, clock):
    fresh = ask_partner(world, clock)
    with tenant_transaction(world.ctx):
        assert code_of(connector.resolve_by_hand, operation_ref=fresh["public_id"],
                       outcome="confirmed", actor_ref="m1") == ErrorCode.UNSAFE_REPLAY
        assert code_of(connector.resolve_by_hand, operation_ref=fresh["public_id"],
                       outcome="maybe", actor_ref="m1") == ErrorCode.VALIDATION_FAILED
        assert code_of(connector.resolve_by_hand, operation_ref="nope",
                       outcome="confirmed", actor_ref="m1") == ErrorCode.NOT_FOUND


def test_an_adapter_that_is_not_enabled_here_is_treated_as_down(world, clock):
    op = ask_partner(world, clock)
    adapters.clear()
    tick(world, clock)
    seen = get(world, op)
    assert (seen["state"], seen["last_error"]) == ("pending", "AdapterUnavailable")


def test_an_operation_is_sent_only_once_however_often_the_worker_runs(world, clock):
    ask_partner(world, clock, sim="confirm")
    assert tick(world, clock)[0] == 1
    assert tick(world, clock)[0] == 0
    assert len(submits()) == 1


# ------------------------------------------------------------------ the record
def test_every_step_is_chained_audited_and_announced(world, clock):
    op = ask_partner(world, clock, sim="confirm")
    tick(world, clock)
    later(world, clock, dt.timedelta(minutes=2))
    with tenant_transaction(world.ctx):
        history = connector.operation_history(op["public_id"])
        assert history["chain_ok"] is True
        assert [a for a in AuditEvent.objects.filter(
            action__startswith="connector.").values_list("action", flat=True)] == [
            "connector.request"]
        events = list(OutboxEvent.objects.filter(
            event_type__startswith="connector.operation.").order_by("seq"))
    assert [e.event_type for e in events] == [
        "connector.operation.accepted.v1", "connector.operation.confirmed.v1"]
    assert set(events[0].payload) == {"operation_ref", "operation_type", "subject_ref", "state"}


def test_the_history_cannot_be_rewritten(world, clock):
    op = ask_partner(world, clock)
    with pytest.raises(DatabaseError), transaction.atomic():
        OperationEvent.objects.update(actor_ref="someone_else")
    with pytest.raises(DatabaseError), transaction.atomic():
        OperationEvent.objects.all().delete()
    trigger = "connector_operationevent_no_change"
    with connection.cursor() as cursor:                         # a superuser tampering on purpose
        cursor.execute(f"ALTER TABLE connector_operationevent DISABLE TRIGGER {trigger}")
        try:
            cursor.execute("UPDATE connector_operationevent SET code = 'forged'")
        finally:
            cursor.execute(f"ALTER TABLE connector_operationevent ENABLE TRIGGER {trigger}")
    with tenant_transaction(world.ctx):
        assert connector.operation_history(op["public_id"])["chain_ok"] is False


def test_redriving_and_settling_are_audited(world, clock):
    op = ask_partner(world, clock, sim="down", sim_fail=50)
    with tenant_transaction(world.ctx):
        ExternalOperation.objects.filter(public_id=op["public_id"]).update(state="dead")
        connector.redrive(operation_ref=op["public_id"], actor_ref="manager1", clock=clock)
        ExternalOperation.objects.filter(public_id=op["public_id"]).update(state="unknown")
        connector.resolve_by_hand(
            operation_ref=op["public_id"], outcome="rejected", actor_ref="manager1", clock=clock)
        actions = sorted(AuditEvent.objects.filter(
            action__startswith="connector.").values_list("action", flat=True))
    assert actions == ["connector.redrive", "connector.request", "connector.resolve"]


# ------------------------------------------------------------------ reading, jobs, tenants
def test_operations_can_be_listed_by_state_and_subject(world, clock):
    ask_partner(world, clock, sim="reject")
    ask_partner(world, clock, sim="confirm_now")
    tick(world, clock)
    with tenant_transaction(world.ctx):
        assert len(connector.list_operations()) == 2
        assert [o["state"] for o in connector.list_operations(state="rejected")] == ["rejected"]
        assert connector.list_operations(subject_ref="BK1") != []
        assert connector.list_operations(subject_ref="other") == []


def test_the_worker_job_drives_every_customers_operations(world):
    other = World()
    first = ask_partner(world, None, sim="confirm_now")        # real clock: due right away
    second = ask_partner(other, None, sim="confirm_now")
    assert jobs.run_all_tenants() == 2
    assert get(world, first)["state"] == "confirmed"
    assert get(other, second)["state"] == "confirmed"
    assert "connector.run" in periodic.registered()


def test_one_customer_cannot_see_or_settle_anothers_operations(world, clock):
    other = World()
    op = ask_partner(world, clock)
    with tenant_transaction(other.ctx):
        assert code_of(connector.get_operation, op["public_id"]) == ErrorCode.NOT_FOUND
        assert code_of(connector.redrive, operation_ref=op["public_id"],
                       actor_ref="m1") == ErrorCode.NOT_FOUND
        assert connector.list_operations() == []
    with tenant_transaction(other.ctx), as_app_role():          # and the database itself agrees
        assert ExternalOperation.objects.count() == 0
        assert OperationEvent.objects.count() == 0
    with tenant_transaction(world.ctx), as_app_role():
        assert ExternalOperation.objects.count() == 1


# ------------------------------------------------------------------ with the workflow engine
def test_only_the_partners_own_confirmation_can_move_a_booking_journey_forward(world, clock):
    workflow.load_bundled_definitions()
    with tenant_transaction(world.ctx):
        journey = workflow.engine.start(
            definition_key="J1.booking", subject_ref="BK1", owner_ref="doc1",
            actor_ref="reception1", clock=clock)

    def try_confirm(evidence, actor_kind):
        with tenant_transaction(world.ctx):
            return workflow.engine.submit_transition(
                instance_ref=journey["public_id"], transition="confirm", actor_ref="worker1",
                actor_kind=actor_kind, expected_version=journey["entity_version"],
                evidence=evidence, clock=clock)

    op = ask_partner(world, clock, sim="confirm")
    tick(world, clock)                                         # the partner says "received"
    assert get(world, op)["source_confirmed"] is False
    receipt_only = [workflow.Evidence("transport_receipt", op["public_id"])]
    assert code_of(try_confirm, receipt_only, "system") == ErrorCode.PARTNER_UNCONFIRMED

    later(world, clock, dt.timedelta(minutes=2))               # now the partner has confirmed
    assert get(world, op)["source_confirmed"] is True
    confirmed = [workflow.Evidence("source_confirmed_outcome", op["public_id"])]
    assert try_confirm(confirmed, "partner")["state"] == "confirmed"


def test_a_person_settling_an_operation_by_hand_is_not_the_partners_confirmation(world, clock):
    op = ask_partner(world, clock, sim="silent")
    with tenant_transaction(world.ctx):
        ExternalOperation.objects.filter(public_id=op["public_id"]).update(max_polls=1)
    tick(world, clock)
    later(world, clock)
    assert get(world, op)["state"] == "dead"
    with tenant_transaction(world.ctx):
        settled = connector.resolve_by_hand(
            operation_ref=op["public_id"], outcome="confirmed", actor_ref="manager1", clock=clock)
    assert settled["state"] == "confirmed"
    assert settled["source_confirmed"] is False                # nothing may treat it as the source
