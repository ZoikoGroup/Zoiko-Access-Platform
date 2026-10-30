import datetime as dt
import threading
import time
import uuid

import pytest
from django.db import connection, transaction

from foundation.cell_context import tenant_transaction
from foundation.models import OutboxEvent, OutboxState
from foundation.outbox import MAX_ATTEMPTS, OutboxError, backoff_seconds, emit, relay_once
from foundation.payloads import PayloadError
from foundation.timeutil import FixedClock, utc_now
from tests.conftest import as_app_role

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("rls_probe")]

EVENT = "booking.partner.confirmed.v1"


def emit_in(ctx, ref="bk_1", **kwargs):
    with tenant_transaction(ctx):
        return emit(event_type=EVENT, aggregate_type="booking", aggregate_ref=ref, **kwargs)


def collector():
    seen = []
    return seen, seen.append


class Crash(BaseException):
    """Simulates the process dying (not an Exception, so nothing catches it)."""


def clock():
    """A controllable clock just ahead of the real one (rows are stamped with real time)."""
    return FixedClock(utc_now() + dt.timedelta(minutes=1))


# ------------------------------------------------------------------ writing
def test_emit_records_a_pending_event_stamped_from_the_context(ctx_a):
    event = emit_in(ctx_a, payload={"booking_ref": "bk_1"})
    stored = OutboxEvent.objects.get(pk=event.pk)
    assert stored.state == OutboxState.PENDING
    assert (stored.cell_id, stored.tenant_id) == (ctx_a.cell_id, ctx_a.tenant_id)
    assert stored.event_id.version == 4          # random, safe to share outside
    assert stored.ordering_key == "booking:bk_1"
    assert stored.payload == {"booking_ref": "bk_1"}
    assert len(stored.correlation_id) >= 8
    assert stored.attempts == 0 and stored.published_at is None


def test_events_get_a_strictly_increasing_delivery_number(ctx_a):
    first = emit_in(ctx_a, ref="bk_1")
    second = emit_in(ctx_a, ref="bk_2")
    seqs = list(OutboxEvent.objects.order_by("created_at").values_list("seq", flat=True))
    assert seqs == sorted(seqs) and seqs[0] < seqs[1]
    assert first.event_id != second.event_id


def test_emit_outside_a_transaction_is_refused():
    assert not connection.in_atomic_block
    with pytest.raises(OutboxError):
        emit(event_type=EVENT, aggregate_type="booking", aggregate_ref="bk_1")


def test_if_the_transaction_rolls_back_the_event_never_existed(ctx_a):
    with pytest.raises(RuntimeError), tenant_transaction(ctx_a):
        emit(event_type=EVENT, aggregate_type="booking", aggregate_ref="bk_1")
        raise RuntimeError("the business change failed")
    assert OutboxEvent.objects.count() == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"event_type": "confirmed"},                          # no version suffix
        {"event_type": "booking confirmed.v1"},               # space
        {"aggregate_ref": "bk 1; drop"},                      # not an identifier
        {"aggregate_type": ""},
    ],
)
def test_malformed_identifiers_are_refused(ctx_a, kwargs):
    args = {"event_type": EVENT, "aggregate_type": "booking", "aggregate_ref": "bk_1", **kwargs}
    with pytest.raises(OutboxError), tenant_transaction(ctx_a):
        emit(**args)


def test_payloads_that_could_carry_data_are_refused(ctx_a):
    with pytest.raises(PayloadError), tenant_transaction(ctx_a):
        emit(event_type=EVENT, aggregate_type="booking", aggregate_ref="bk_1",
             payload={"note": "John Smith has HIV " * 20})


def test_a_dedupe_key_makes_emitting_twice_the_same_event(ctx_a, ctx_b):
    one = emit_in(ctx_a, dedupe_key="confirm-bk_1")
    two = emit_in(ctx_a, dedupe_key="confirm-bk_1")
    assert one.event_id == two.event_id
    assert OutboxEvent.objects.filter(tenant_id=ctx_a.tenant_id).count() == 1
    other_tenant = emit_in(ctx_b, dedupe_key="confirm-bk_1")   # keys are per tenant
    assert other_tenant.event_id != one.event_id


def test_emit_works_as_the_restricted_app_role(ctx_a):
    with tenant_transaction(ctx_a), as_app_role():
        emit(event_type=EVENT, aggregate_type="booking", aggregate_ref="bk_1")
    assert OutboxEvent.objects.count() == 1


# ------------------------------------------------------------------ delivery
def test_relay_delivers_and_marks_published(ctx_a):
    emit_in(ctx_a, payload={"booking_ref": "bk_1"})
    seen, publish = collector()
    result = relay_once(publish)
    assert (result.published, result.retrying, result.quarantined) == (1, 0, 0)
    assert seen[0].event_type == EVENT
    assert seen[0].payload == {"booking_ref": "bk_1"}
    assert seen[0].tenant_id == ctx_a.tenant_id
    row = OutboxEvent.objects.get()
    assert row.state == OutboxState.PUBLISHED and row.published_at is not None
    assert relay_once(publish).published == 0          # nothing is sent twice
    assert len(seen) == 1


def test_different_keys_go_in_one_batch_in_order(ctx_a):
    emit_in(ctx_a, ref="bk_1")
    emit_in(ctx_a, ref="bk_2")
    emit_in(ctx_a, ref="bk_3")
    seen, publish = collector()
    assert relay_once(publish).published == 3
    assert [e.aggregate_ref for e in seen] == ["bk_1", "bk_2", "bk_3"]


def test_events_for_the_same_thing_are_delivered_in_order(ctx_a):
    emit_in(ctx_a, ref="bk_1", payload={"step": 1})
    emit_in(ctx_a, ref="bk_1", payload={"step": 2})
    seen, publish = collector()
    relay_once(publish)
    assert [e.payload["step"] for e in seen] == [1]     # second waits for the first
    relay_once(publish)
    assert [e.payload["step"] for e in seen] == [1, 2]


def test_a_failure_is_retried_later_and_stores_no_message(ctx_a):
    emit_in(ctx_a)
    now = clock()

    def fail(event):
        raise ValueError("patient John Smith, NHS 943 476 5919")

    result = relay_once(fail, clock=now)
    assert (result.published, result.retrying) == (0, 1)
    row = OutboxEvent.objects.get()
    assert row.state == OutboxState.PENDING and row.attempts == 1
    assert row.last_error == "ValueError"                # the class only
    assert "John" not in row.last_error
    assert row.next_attempt_at > now.now()

    seen, publish = collector()
    assert relay_once(publish, clock=now).published == 0   # not due yet
    now.advance(dt.timedelta(minutes=5))
    assert relay_once(publish, clock=now).published == 1   # due: delivered
    assert OutboxEvent.objects.get().state == OutboxState.PUBLISHED


def test_a_failing_event_holds_back_later_events_for_the_same_thing(ctx_a):
    emit_in(ctx_a, ref="bk_1", payload={"step": 1})
    emit_in(ctx_a, ref="bk_1", payload={"step": 2})
    emit_in(ctx_a, ref="bk_9", payload={"step": 1})
    now = clock()
    seen, publish = collector()

    def flaky(event):
        if event.aggregate_ref == "bk_1":
            raise ConnectionError
        publish(event)

    relay_once(flaky, clock=now)
    assert [e.aggregate_ref for e in seen] == ["bk_9"]   # the other thing is not blocked
    now.advance(dt.timedelta(minutes=5))
    relay_once(flaky, clock=now)
    held = [(e.aggregate_ref, e.payload["step"]) for e in seen]
    assert held == [("bk_9", 1)]                         # step 2 is still held back


def test_after_too_many_failures_the_event_is_quarantined_and_keeps_blocking(ctx_a):
    emit_in(ctx_a, ref="bk_1", payload={"step": 1})
    emit_in(ctx_a, ref="bk_1", payload={"step": 2})
    now = clock()

    def always_fail(event):
        raise ConnectionError

    for _ in range(MAX_ATTEMPTS):
        relay_once(always_fail, clock=now)
        now.advance(dt.timedelta(hours=2))
    first = OutboxEvent.objects.order_by("seq").first()
    assert first.state == OutboxState.QUARANTINED and first.attempts == MAX_ATTEMPTS

    seen, publish = collector()
    now.advance(dt.timedelta(days=1))
    assert relay_once(publish, clock=now).published == 0     # step 2 must not jump the queue
    assert seen == []


def test_a_crash_after_sending_means_it_is_sent_again_at_least_once(ctx_a):
    emit_in(ctx_a)
    sent = []

    def publish_then_die(event):
        sent.append(event.event_id)
        raise Crash

    with pytest.raises(Crash):
        relay_once(publish_then_die)
    assert OutboxEvent.objects.get().state == OutboxState.PENDING   # not recorded as done
    relay_once(lambda event: sent.append(event.event_id))
    assert len(sent) == 2 and sent[0] == sent[1]              # the SAME event id: a duplicate


def test_two_relays_never_claim_the_same_event(ctx_a):
    emit_in(ctx_a)
    started, release = threading.Event(), threading.Event()
    first_saw, second_saw = [], []

    def slow(event):
        started.set()
        release.wait(10)
        first_saw.append(event.event_id)

    def run_first():
        try:
            relay_once(slow)
        finally:
            connection.close()

    thread = threading.Thread(target=run_first)
    thread.start()
    assert started.wait(10)
    began = time.monotonic()
    second = relay_once(second_saw.append)    # the row is locked by the first relay: skipped
    waited = time.monotonic() - began
    release.set()
    thread.join(10)
    assert second.published == 0 and second_saw == []
    assert waited < 3                         # it moved on at once instead of queueing behind
    assert len(first_saw) == 1
    assert OutboxEvent.objects.get().state == OutboxState.PUBLISHED


def test_relay_works_as_the_restricted_app_role(ctx_a):
    emit_in(ctx_a)
    seen, publish = collector()
    with transaction.atomic(), as_app_role():
        assert relay_once(publish).published == 1
    assert len(seen) == 1


# ------------------------------------------------------------------ retry timing
def test_backoff_doubles_and_is_capped():
    assert [backoff_seconds(n, jitter=1.0) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
    assert backoff_seconds(30, jitter=1.0) == 3600
    assert 4 <= backoff_seconds(1) <= 6                      # jitter stays within +-20%


def test_event_ids_are_random_uuid4(ctx_a):
    ids = {emit_in(ctx_a, ref=f"bk_{i}").event_id for i in range(20)}
    assert len(ids) == 20 and all(isinstance(i, uuid.UUID) and i.version == 4 for i in ids)
