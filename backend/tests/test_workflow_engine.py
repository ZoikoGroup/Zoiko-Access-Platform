import copy
import datetime as dt
import json
import uuid
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from foundation.cell_context import build_context, tenant_transaction
from foundation.errors import ErrorCode, ZoikoError
from foundation.models import OutboxEvent
from foundation.timeutil import UTC, FixedClock
from modules.evidence.models import AuditEvent
from modules.trust import api as trust
from modules.workflow import api as workflow
from modules.workflow import jobs
from modules.workflow.api import Evidence
from modules.workflow.models import (
    InstanceStatus,
    TimerStatus,
    WorkflowDefinition,
    WorkflowException,
    WorkflowInstance,
    WorkflowTimer,
    WorkflowTransition,
)
from tests.conftest import as_app_role

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

T0 = dt.datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
BUNDLED = Path(workflow.__file__).parent / "bundled" / "j1_booking.v1.json"
MINIMAL = {
    "key": "T.simple", "version": 1, "initial": "a", "states": {"a": {}, "b": {}},
    "terminal": {"b": "completed"}, "transitions": {"go": {"from": ["a"], "to": "b"}},
}


def make_tenant():
    tenant = trust.create_tenant(
        name=f"Flow {uuid.uuid4().hex[:8]}", type=trust.TenantType.PROVIDER_ORG)
    return build_context(tenant.id)


@pytest.fixture
def ctx():
    return make_tenant()


@pytest.fixture
def defs():
    return workflow.load_bundled_definitions()


@pytest.fixture
def clock():
    return FixedClock(T0)


def start(ctx, clock, subject="BK1", owner="doc1", **over):
    with tenant_transaction(ctx):
        return workflow.engine.start(
            definition_key="J1.booking", subject_ref=subject, owner_ref=owner,
            actor_ref="reception1", clock=clock, **over)


def move(ctx, inst, transition, *, actor="reception1", kind="person", evidence=None,
         clock=None, version=None):
    with tenant_transaction(ctx):
        return workflow.engine.submit_transition(
            instance_ref=inst["public_id"], transition=transition, actor_ref=actor,
            actor_kind=kind, expected_version=version or inst["entity_version"],
            evidence=evidence, clock=clock)


def confirm(ctx, inst, clock=None):
    return move(ctx, inst, "confirm", actor="partner1", kind="partner", clock=clock,
                evidence=[Evidence("source_confirmed_outcome", "EV-confirm-1")])


def code_of(call, *args, **kwargs):
    with pytest.raises(ZoikoError) as caught:
        call(*args, **kwargs)
    return caught.value.code


# ------------------------------------------------------------------ definitions
def test_the_bundled_definition_publishes_once_and_is_idempotent(defs):
    again = workflow.load_bundled_definitions()
    assert again[0]["public_id"] == defs[0]["public_id"]
    assert sorted(d["key"] for d in workflow.list_definitions()) == [
        "J1.booking", "J1.partner_booking", "J2.referral", "J3.waitlist"]


def test_a_released_version_cannot_be_republished_with_different_content(defs):
    body = json.loads(BUNDLED.read_text(encoding="utf8"))
    body["timers"]["confirm_by"]["after_minutes"] = 5
    assert code_of(lambda: workflow.publish_definition(body)) == ErrorCode.CONFLICT_VERSION


def test_the_database_refuses_to_change_or_remove_a_released_definition(defs):
    with pytest.raises(DatabaseError), transaction.atomic():
        WorkflowDefinition.objects.update(body={"key": "x"})
    with pytest.raises(DatabaseError), transaction.atomic():
        WorkflowDefinition.objects.all().delete()
    workflow.retire_definition("J1.booking", 1)          # its state may move; its content may not
    assert WorkflowDefinition.objects.get(key="J1.booking").state == "retired"


def test_a_retired_version_starts_nothing_new_but_running_journeys_carry_on(ctx, defs, clock):
    running = start(ctx, clock)
    workflow.retire_definition("J1.booking", 1)
    assert code_of(lambda: start(ctx, clock, subject="BK2")) == ErrorCode.NOT_FOUND
    done = move(ctx, running, "cancel", clock=clock)
    assert done["status"] == "cancelled"


def test_the_minimal_definition_is_valid():
    assert workflow.validate_definition(MINIMAL) == MINIMAL


@pytest.mark.parametrize("name,change", [
    ("initial is not a state", lambda b: b.update(initial="zzz")),
    ("no terminal state", lambda b: b.update(terminal={})),
    ("terminal state unknown", lambda b: b.update(terminal={"zzz": "completed"})),
    ("terminal outcome unknown", lambda b: b.update(terminal={"b": "vanished"})),
    ("unreachable state", lambda b: b["states"].update(orphan={})),
    ("a transition leaves a finished journey",
     lambda b: b["transitions"].update(back={"from": ["b"], "to": "a"})),
    ("unknown guard", lambda b: b["transitions"]["go"].update(guards=["trust_me"])),
    ("unknown evidence kind", lambda b: b["transitions"]["go"].update(requires_evidence=["vibes"])),
    ("transition to nowhere", lambda b: b["transitions"]["go"].update(to="zzz")),
    ("transition from nowhere", lambda b: b["transitions"]["go"].update({"from": ["zzz"]})),
    ("timer on an unknown state", lambda b: b.update(timers={"t": {"state": "zzz",
                                                                    "after_minutes": 5}})),
    ("timer of zero minutes", lambda b: b.update(timers={"t": {"state": "a",
                                                                "after_minutes": 0}})),
    ("bad key", lambda b: b.update(key="1 bad key")),
    ("version zero", lambda b: b.update(version=0)),
    ("version is a boolean", lambda b: b.update(version=True)),
    ("state name has capitals", lambda b: b["states"].update(Bad={})),
])
def test_a_definition_that_could_not_run_safely_is_refused(name, change):
    body = copy.deepcopy(MINIMAL)
    change(body)
    assert code_of(lambda: workflow.validate_definition(body)) == ErrorCode.VALIDATION_FAILED, name


def test_something_that_is_not_a_definition_is_refused():
    assert code_of(lambda: workflow.validate_definition("nope")) == ErrorCode.VALIDATION_FAILED


# ------------------------------------------------------------------ the journey
def test_a_journey_runs_from_request_to_a_completed_visit(ctx, defs, clock):
    inst = start(ctx, clock)
    assert (inst["state"], inst["status"], inst["entity_version"]) == ("requested", "running", 1)
    inst = confirm(ctx, inst, clock)
    assert (inst["state"], inst["entity_version"]) == ("confirmed", 2)
    inst = move(ctx, inst, "attend", actor="doc1", kind="clinician", clock=clock,
                evidence=[Evidence("professional_acknowledgment", "EV-ack-1")])
    assert (inst["state"], inst["status"]) == ("attended", "completed")
    assert inst["completed_at"] is not None
    with tenant_transaction(ctx):
        steps = workflow.instance_history(inst["public_id"])
        assert [s["transition"] for s in steps["steps"]] == ["start", "confirm", "attend"]
        assert steps["chain_ok"] is True
        assert steps["steps"][1]["evidence"] == [
            {"kind": "source_confirmed_outcome", "ref": "EV-confirm-1"}]
        assert WorkflowTimer.objects.filter(status="pending").count() == 0   # nothing left waiting


def test_every_step_is_audited_and_announced(ctx, defs, clock):
    inst = start(ctx, clock)
    confirm(ctx, inst, clock)
    with tenant_transaction(ctx):
        actions = sorted(AuditEvent.objects.filter(
            action__startswith="workflow.").values_list("action", flat=True))
        assert actions == ["workflow.start", "workflow.transition"]
        kinds = sorted(OutboxEvent.objects.filter(
            event_type__startswith="workflow.").values_list("event_type", flat=True))
        assert kinds == ["workflow.instance.started.v1", "workflow.instance.transitioned.v1"]


def test_only_one_journey_of_a_kind_runs_for_a_subject(ctx, defs, clock):
    first = start(ctx, clock)
    assert code_of(lambda: start(ctx, clock)) == ErrorCode.CONFLICT_VERSION
    move(ctx, first, "cancel", clock=clock)
    assert start(ctx, clock)["status"] == "running"        # the old one ended: a new may run


def test_starting_validates_its_inputs(ctx, defs, clock):
    assert code_of(lambda: start(ctx, clock, zone="Mars/Olympus")) == ErrorCode.VALIDATION_FAILED
    assert code_of(lambda: start(ctx, clock, subject="has spaces")) == ErrorCode.VALIDATION_FAILED
    with tenant_transaction(ctx):
        missing = lambda: workflow.engine.start(  # noqa: E731
            definition_key="No.such", subject_ref="X1", owner_ref="o", actor_ref="a")
        assert code_of(missing) == ErrorCode.NOT_FOUND


# ------------------------------------------------------------------ evidence: the heart of it
@pytest.fixture
def confirmed(ctx, defs, clock):
    return confirm(ctx, start(ctx, clock), clock)


def state_of(ctx, inst):
    with tenant_transaction(ctx):
        return workflow.engine.get_instance(inst["public_id"])["state"]


def test_confirming_needs_the_partners_own_confirmation(ctx, defs, clock):
    inst = start(ctx, clock)
    cases = [
        ("no evidence", "partner", None),
        ("a transport receipt is not a confirmation", "system",
         [Evidence("transport_receipt", "EV-r1")]),
        ("the system cannot confirm for the partner", "system",
         [Evidence("source_confirmed_outcome", "EV-c1")]),
        ("a clinician is not the source", "clinician",
         [Evidence("source_confirmed_outcome", "EV-c1")]),
        ("a viewed screen is not a confirmation", "person",
         [Evidence("authenticated_view", "EV-v1")]),
    ]
    for label, kind, evidence in cases:
        code = code_of(move, ctx, inst, "confirm", actor="x1", kind=kind,
                       evidence=evidence, clock=clock)
        assert code == ErrorCode.PARTNER_UNCONFIRMED, label
    assert state_of(ctx, inst) == "requested"                  # none of it moved the journey


def test_attending_needs_a_clinicians_acknowledgment_not_a_receipt(ctx, confirmed, clock):
    ack = [Evidence("professional_acknowledgment", "EV-ack-1")]
    cases = [
        ("no evidence", "clinician", None),
        ("a transport receipt in its place", "system", [Evidence("transport_receipt", "EV-r1")]),
        ("a viewed screen in its place", "clinician", [Evidence("authenticated_view", "EV-v1")]),
        ("the system cannot acknowledge for a clinician", "system", ack),
        ("a patient cannot acknowledge for a clinician", "person", ack),
    ]
    for label, kind, evidence in cases:
        code = code_of(move, ctx, confirmed, "attend", actor="doc1", kind=kind,
                       evidence=evidence, clock=clock)
        assert code == ErrorCode.CLINICAL_ACCEPTANCE_ABSENT, label
    assert state_of(ctx, confirmed) == "confirmed"


def test_only_the_accountable_owner_may_close_the_visit(ctx, confirmed, clock):
    code = code_of(lambda: move(
        ctx, confirmed, "attend", actor="someone_else", kind="clinician", clock=clock,
        evidence=[Evidence("professional_acknowledgment", "EV-ack-1")]))
    assert code == ErrorCode.POLICY_DENIED


def test_a_system_process_cannot_cancel_on_behalf_of_a_person(ctx, defs, clock):
    inst = start(ctx, clock)
    code = code_of(lambda: move(ctx, inst, "cancel", actor="job1", kind="system", clock=clock))
    assert code == ErrorCode.POLICY_DENIED


def test_malformed_evidence_or_actors_are_refused(ctx, defs, clock):
    inst = start(ctx, clock)
    for evidence in ([Evidence("vibes", "EV-1")], [Evidence("transport_receipt", "bad ref!")]):
        assert code_of(move, ctx, inst, "cancel", kind="person", evidence=evidence,
                       clock=clock) == ErrorCode.VALIDATION_FAILED
    assert code_of(lambda: move(ctx, inst, "cancel", kind="robot",
                                clock=clock)) == ErrorCode.VALIDATION_FAILED


# ------------------------------------------------------------------ concurrency and state rules
def test_two_people_acting_on_the_same_version_cannot_both_win(ctx, defs, clock):
    inst = start(ctx, clock)
    move(ctx, inst, "cancel", clock=clock)                      # the first one wins
    assert code_of(lambda: move(ctx, inst, "cancel", clock=clock)) == ErrorCode.CONFLICT_VERSION


def test_a_stale_version_is_refused_even_when_the_move_is_allowed(ctx, defs, clock):
    inst = start(ctx, clock)
    assert code_of(lambda: move(ctx, inst, "cancel", version=99, clock=clock)
                   ) == ErrorCode.CONFLICT_VERSION


def test_a_move_the_definition_does_not_allow_is_refused(ctx, defs, clock):
    inst = start(ctx, clock)
    assert code_of(lambda: move(ctx, inst, "attend", actor="doc1", kind="clinician",
                                clock=clock)) == ErrorCode.VALIDATION_FAILED   # not from requested
    assert code_of(lambda: move(ctx, inst, "no_such_step", clock=clock)
                   ) == ErrorCode.VALIDATION_FAILED


def test_nothing_moves_after_the_journey_has_ended(ctx, defs, clock):
    inst = start(ctx, clock)
    done = move(ctx, inst, "cancel", clock=clock)
    assert code_of(lambda: move(ctx, done, "cancel", clock=clock)) == ErrorCode.CONFLICT_VERSION


def test_a_running_journey_keeps_the_version_it_started_on(ctx, defs, clock):
    old = start(ctx, clock)
    body = json.loads(BUNDLED.read_text(encoding="utf8"))
    body["version"] = 2
    del body["transitions"]["cancel"]                           # v2 no longer allows cancelling
    del body["states"]["cancelled"]
    del body["terminal"]["cancelled"]
    workflow.publish_definition(body)
    assert move(ctx, old, "cancel", clock=clock)["status"] == "cancelled"   # still v1's rules
    fresh = start(ctx, clock, subject="BK-new")
    assert fresh["definition_version"] == 2
    assert code_of(lambda: move(ctx, fresh, "cancel", clock=clock)) == ErrorCode.VALIDATION_FAILED


# ------------------------------------------------------------------ the history cannot be rewritten
def test_the_database_refuses_to_rewrite_a_transition(ctx, defs, clock):
    start(ctx, clock)
    with pytest.raises(DatabaseError), transaction.atomic():
        WorkflowTransition.objects.update(actor_ref="someone_else")
    with pytest.raises(DatabaseError), transaction.atomic():
        WorkflowTransition.objects.all().delete()


def test_the_chain_notices_a_row_changed_behind_the_applications_back(ctx, defs, clock):
    inst = move(ctx, start(ctx, clock), "cancel", clock=clock)
    trigger = "workflow_workflowtransition_no_change"
    with connection.cursor() as cursor:                         # a superuser tampering on purpose
        cursor.execute(f"ALTER TABLE workflow_workflowtransition DISABLE TRIGGER {trigger}")
        try:
            cursor.execute(
                "UPDATE workflow_workflowtransition SET actor_ref = 'forged' WHERE seq = 2")
        finally:
            cursor.execute(f"ALTER TABLE workflow_workflowtransition ENABLE TRIGGER {trigger}")
    with tenant_transaction(ctx):
        report = workflow.instance_history(inst["public_id"])
    assert (report["chain_ok"], report["bad_seq"]) == (False, 2)


# ------------------------------------------------------------------ timers
def test_starting_a_journey_sets_the_deadline_for_its_first_state(ctx, defs, clock):
    start(ctx, clock)
    with tenant_transaction(ctx):
        (timer,) = WorkflowTimer.objects.all()
        assert (timer.name, timer.state_name, timer.zone) == ("confirm_by", "requested", "UTC")
        assert timer.deadline_at == T0 + dt.timedelta(minutes=1440)


def test_a_deadline_that_passes_raises_an_exception_and_moves_nothing(ctx, defs, clock):
    inst = start(ctx, clock)
    with tenant_transaction(ctx):
        assert workflow.engine.fire_due_timers(clock=clock) == 0     # not due yet
    clock.advance(dt.timedelta(hours=25))
    with tenant_transaction(ctx):
        assert workflow.engine.fire_due_timers(clock=clock) == 1
        (exc,) = workflow.list_exceptions()
        assert (exc["detail"], exc["status"], exc["instance_ref"]) == (
            "confirm_by", "open", inst["public_id"])
        assert workflow.engine.get_instance(inst["public_id"])["state"] == "requested"
        assert OutboxEvent.objects.filter(event_type="workflow.timer.fired.v1").count() == 1
        assert workflow.engine.fire_due_timers(clock=clock) == 0     # a timer fires only once


def test_an_exception_can_be_resolved_by_a_person_and_resolving_twice_is_harmless(ctx, defs,
                                                                                  clock):
    start(ctx, clock)
    clock.advance(dt.timedelta(days=2))
    with tenant_transaction(ctx):
        workflow.engine.fire_due_timers(clock=clock)
        (exc,) = workflow.list_exceptions()
        workflow.resolve_exception(exc["public_id"], actor_ref="manager1", clock=clock)
        workflow.resolve_exception(exc["public_id"], actor_ref="manager2", clock=clock)
        assert workflow.list_exceptions() == []
        row = WorkflowException.objects.get()
        assert (row.status, row.resolved_by_ref) == ("resolved", "manager1")
        assert code_of(lambda: workflow.resolve_exception("nope", actor_ref="m")
                       ) == ErrorCode.NOT_FOUND


def test_moving_on_cancels_the_old_deadline_and_starts_the_next(ctx, defs, clock):
    inst = start(ctx, clock)
    confirm(ctx, inst, clock)
    with tenant_transaction(ctx):
        by_name = {t.name: t for t in WorkflowTimer.objects.all()}
        assert by_name["confirm_by"].status == TimerStatus.CANCELLED
        assert by_name["attendance_by"].status == TimerStatus.PENDING
    clock.advance(dt.timedelta(days=2))             # past the old deadline: it must not fire
    with tenant_transaction(ctx):
        assert workflow.engine.fire_due_timers(clock=clock) == 0
    clock.advance(dt.timedelta(days=6))             # past the new one
    with tenant_transaction(ctx):
        assert workflow.engine.fire_due_timers(clock=clock) == 1
        assert workflow.list_exceptions()[0]["detail"] == "attendance_by"


def test_a_deadline_that_passes_after_the_journey_ended_does_nothing(ctx, defs, clock):
    inst = start(ctx, clock)
    move(ctx, inst, "cancel", clock=clock)
    clock.advance(dt.timedelta(days=30))
    with tenant_transaction(ctx):
        assert workflow.engine.fire_due_timers(clock=clock) == 0
        assert WorkflowException.objects.count() == 0


def test_a_timer_left_pending_for_a_finished_journey_is_quietly_cancelled(ctx, defs, clock):
    inst = start(ctx, clock)
    clock.advance(dt.timedelta(days=2))
    with tenant_transaction(ctx):               # simulate a timer that escaped cancellation
        WorkflowInstance.objects.filter(public_id=inst["public_id"]).update(
            status=InstanceStatus.CANCELLED)
        assert workflow.engine.fire_due_timers(clock=clock) == 0
        assert WorkflowTimer.objects.get().status == TimerStatus.CANCELLED
        assert WorkflowException.objects.count() == 0


def test_the_worker_job_fires_due_deadlines_for_every_customer(defs):
    ctx_a, ctx_b = make_tenant(), make_tenant()
    for ctx in (ctx_a, ctx_b):
        start(ctx, FixedClock(dt.datetime(2020, 1, 1, tzinfo=UTC)))   # long ago: overdue now
    assert jobs.fire_timers_all_tenants() == 2
    assert jobs.fire_timers_all_tenants() == 0


# ------------------------------------------------------------------ tenants stay apart
def test_one_customer_cannot_see_or_move_anothers_journeys(defs, clock):
    ctx_a, ctx_b = make_tenant(), make_tenant()
    inst = start(ctx_a, clock)
    with tenant_transaction(ctx_b):
        assert code_of(lambda: workflow.engine.get_instance(inst["public_id"])
                       ) == ErrorCode.NOT_FOUND
    assert code_of(lambda: move(ctx_b, inst, "cancel", clock=clock)) == ErrorCode.NOT_FOUND


def test_row_level_security_hides_other_customers_journeys_from_the_app_login(defs, clock):
    ctx_a, ctx_b = make_tenant(), make_tenant()
    start(ctx_a, clock)
    with tenant_transaction(ctx_b), as_app_role():
        assert WorkflowInstance.objects.count() == 0
        assert WorkflowTimer.objects.count() == 0
    with tenant_transaction(ctx_a), as_app_role():
        assert WorkflowInstance.objects.count() == 1


def test_the_default_engine_is_the_database_engine():
    assert isinstance(workflow.engine, workflow.DbWorkflowEngine)
