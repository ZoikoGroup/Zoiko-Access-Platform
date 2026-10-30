import datetime as dt
import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError

from foundation import dispatch
from foundation.cell_context import require_context, tenant_transaction
from foundation.dispatch import clear_subscriptions, subscribe
from foundation.management.commands import relay_outbox
from foundation.models import InboxMessage, OutboxEvent, OutboxState
from foundation.outbox import RelayResult, _to_published, backlog_stats, emit, relay_once
from foundation.timeutil import FixedClock, utc_now
from tests.probe.models import Probe
from tests.test_policy import World

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]


@pytest.fixture(autouse=True)
def empty_registry():
    """Each test starts with no subscribers, and the real ones are put back afterwards."""
    saved = dispatch.subscriptions()
    clear_subscriptions()
    yield
    clear_subscriptions()
    for sub in saved:
        subscribe(sub.consumer, sub.prefix, sub.handler)


@pytest.fixture
def world():
    return World()


def send(world, event_type="booking.confirmed.v1", ref="bk_1", payload=None):
    with tenant_transaction(world.ctx):
        return emit(event_type=event_type, aggregate_type="booking", aggregate_ref=ref,
                    payload=payload or {"booking_ref": ref})


def later(minutes=5):
    return FixedClock(utc_now() + dt.timedelta(minutes=minutes))


def probes(world):
    with tenant_transaction(world.ctx):
        return Probe.objects.filter(tenant_id=world.ctx.tenant_id).count()


def inbox(world):
    with tenant_transaction(world.ctx):
        return InboxMessage.objects.filter(tenant_id=world.ctx.tenant_id).count()


# ------------------------------------------------------------------ registry
def test_subscriptions_are_validated_and_names_cannot_repeat():
    subscribe("notifier", "booking.", lambda e: None)
    with pytest.raises(ValueError):
        subscribe("notifier", "other.", lambda e: None)
    for consumer, prefix in (("X", "booking."), ("ab", "booking."), ("good-name", ""),
                             ("good-name", "bad prefix"), ("has space", "booking.")):
        with pytest.raises(ValueError):
            subscribe(consumer, prefix, lambda e: None)
    assert [s.consumer for s in dispatch.subscriptions()] == ["notifier"]


# ------------------------------------------------------------------ delivery
def test_a_matching_subscriber_gets_the_event_in_that_events_tenant(world):
    seen = []
    subscribe("notifier", "booking.", lambda e: seen.append((e.event_type, require_context())))
    send(world)
    send(world, event_type="slot.created.v1", ref="sl_1")         # not matching
    assert relay_once(dispatch.dispatch).published == 2
    assert [kind for kind, _ in seen] == ["booking.confirmed.v1"]
    assert seen[0][1] == world.ctx
    assert inbox(world) == 1
    with tenant_transaction(world.ctx):
        assert OutboxEvent.objects.filter(state=OutboxState.PUBLISHED).count() == 2


def test_each_tenants_events_run_in_their_own_tenant(world):
    other = World()
    seen = []
    subscribe("notifier", "booking.", lambda e: seen.append(require_context().tenant_id))
    send(world, ref="bk_a")
    send(other, ref="bk_b")
    relay_once(dispatch.dispatch)
    assert sorted(seen) == sorted([world.ctx.tenant_id, other.ctx.tenant_id])


def test_handlers_write_their_own_tenants_data_only(world):
    other = World()
    subscribe("recorder", "booking.", lambda e: Probe.objects.create(label=e.aggregate_ref))
    send(world, ref="bk_a")
    send(other, ref="bk_b")
    relay_once(dispatch.dispatch)
    assert probes(world) == 1 and probes(other) == 1
    with tenant_transaction(world.ctx):
        assert Probe.objects.get(tenant_id=world.ctx.tenant_id).label == "bk_a"


def test_an_event_nobody_subscribes_to_is_simply_delivered(world):
    send(world)
    assert relay_once(dispatch.dispatch).published == 1
    assert backlog_stats()["pending"] == 0 and inbox(world) == 0


def test_a_duplicate_delivery_runs_the_handler_once(world):
    calls = []
    subscribe("notifier", "booking.", lambda e: calls.append(e.event_id))
    event = send(world)
    published = relay_once(dispatch.dispatch)
    assert published.published == 1
    with tenant_transaction(world.ctx):                      # the same event arrives again
        row = OutboxEvent.objects.get(pk=event.pk)
    dispatch.dispatch(_to_published(row))
    assert len(calls) == 1


# ------------------------------------------------------------------ failure and retry
def test_a_failing_handler_changes_nothing_then_succeeds_on_retry(world):
    state = {"fail": True}

    def handler(event):
        Probe.objects.create(label="half-done")
        if state["fail"]:
            raise RuntimeError("boom for patient John Smith")

    subscribe("flaky", "booking.", handler)
    send(world)
    first = relay_once(dispatch.dispatch)
    assert (first.published, first.retrying) == (0, 1)
    assert probes(world) == 0 and inbox(world) == 0          # the half-done work was rolled back
    with tenant_transaction(world.ctx):
        row = OutboxEvent.objects.get()
        assert row.state == OutboxState.PENDING and row.last_error == "RuntimeError"
        assert "John" not in row.last_error                   # class name only
    state["fail"] = False
    second = relay_once(dispatch.dispatch, clock=later())
    assert second.published == 1 and probes(world) == 1 and inbox(world) == 1


def test_a_subscriber_that_already_succeeded_is_not_run_again_on_the_retry(world):
    good_calls, state = [], {"fail": True}
    subscribe("alpha-good", "booking.", lambda e: good_calls.append(1))

    def flaky(event):
        if state["fail"]:
            raise ConnectionError

    subscribe("beta-flaky", "booking.", flaky)
    send(world)
    assert relay_once(dispatch.dispatch).retrying == 1
    state["fail"] = False
    assert relay_once(dispatch.dispatch, clock=later()).published == 1
    assert good_calls == [1]                                  # once, not twice


# ------------------------------------------------------------------ the worker command
def test_the_command_drains_the_backlog_and_prints_counts_only(world):
    send(world, payload={"booking_ref": "bk_SECRETVALUE1"})
    assert backlog_stats()["pending"] == 1
    out = io.StringIO()
    call_command("relay_outbox", "--once", stdout=out)
    assert backlog_stats()["pending"] == 0
    text = out.getvalue()
    assert "published=1" in text and "published 1 event(s)" in text
    assert "SECRETVALUE1" not in text and "bk_" not in text


def test_a_failing_event_is_reported_without_its_error_message(world):
    def explode(event):
        raise ValueError("secret clinical detail")

    subscribe("explode", "booking.", explode)
    send(world)
    out = io.StringIO()
    call_command("relay_outbox", "--once", stdout=out)
    assert "retrying=1" in out.getvalue() and "secret clinical detail" not in out.getvalue()
    assert backlog_stats()["pending"] == 1


def test_the_command_refuses_nonsense_settings():
    bad = (["--interval", "0"], ["--interval", "99999"], ["--batch", "0"], ["--batch", "5000"])
    for args in bad:
        with pytest.raises(CommandError):
            call_command("relay_outbox", "--once", *args)


class FakeStop:
    """Stands in for threading.Event: records how long the loop rested, and stops after `limit`."""

    def __init__(self, limit):
        self.limit, self.waits = limit, []

    def wait(self, seconds):
        self.waits.append(seconds)
        return len(self.waits) >= self.limit


def test_the_worker_rides_out_a_database_outage_and_keeps_going(monkeypatch):
    calls = []

    def flaky_relay(publisher, *, batch_size):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("connection lost: details that must not be printed")
        return RelayResult(published=3, retrying=0, quarantined=0)

    monkeypatch.setattr(relay_outbox, "relay_once", flaky_relay)
    lines, stop = [], FakeStop(limit=2)
    total = relay_outbox.run_loop(stop, interval=1.0, batch=50, once=False, log=lines.append)
    assert total == 3 and len(calls) == 2
    assert stop.waits[0] == relay_outbox.DATABASE_RETRY_SECONDS       # backed off after the outage
    assert any("database not available" in line for line in lines)
    assert not any("details that must not be printed" in line for line in lines)


def test_a_full_batch_means_go_again_at_once(monkeypatch):
    monkeypatch.setattr(relay_outbox, "relay_once",
                        lambda publisher, *, batch_size: RelayResult(batch_size, 0, 0))
    stop = FakeStop(limit=2)
    relay_outbox.run_loop(stop, interval=7.0, batch=10, once=False, log=lambda text: None)
    assert stop.waits == [0.0, 0.0]


def test_once_mode_reports_a_database_problem_as_an_error(monkeypatch):
    def down(publisher, *, batch_size):
        raise OperationalError("down")

    monkeypatch.setattr(relay_outbox, "relay_once", down)
    with pytest.raises(CommandError):
        call_command("relay_outbox", "--once")


def test_the_platform_backlog_returns_to_zero_after_a_pass(world):
    for ref in ("a", "b", "c"):
        send(world, ref=ref)
    before = backlog_stats()
    assert before["pending"] == 3 and before["oldest_pending_seconds"] is not None
    call_command("relay_outbox", "--once", stdout=io.StringIO())
    after = backlog_stats()
    assert after == {"pending": 0, "quarantined": 0, "oldest_pending_seconds": None}
